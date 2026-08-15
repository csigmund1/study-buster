"""Text-occlusion span selection, filtering, and span -> box geometry.

Selection is batched with low-resolution previews and exact OCR text. Geometry
is derived deterministically from the OCR words a returned phrase locates.
"""

import base64
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.config import get_settings
from app.models import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord
from app.services.occlusion_pipeline import (
    PendingOcclusion,
    select_text_occlusions,
    shortlist_diagram_pages,
)
from app.services.text_occlusion import (
    AcceptedSpan,
    AuditDecision,
    BatchAudit,
    CandidateBatch,
    CandidateSpan,
    MockTextSpanSelector,
    ModelSpanRef,
    RefGroup,
    SelectedSpan,
    SpanRef,
    TextPage,
    accept_spans,
    detect_chrome_lines,
    detect_objectives,
    get_text_span_selector,
    is_chrome,
)
from app.services.text_occlusion import filters as filters_module
from app.services.text_occlusion.anthropic import OBJECTIVES_PREFACE, AnthropicTextSpanSelector
from app.services.text_occlusion.document_context import MAX_CHROME_LINE_WORDS, MIN_OBJECTIVE_ITEMS
from app.services.text_occlusion.filters import (
    MAX_SPAN_WORDS,
    MAX_SPANS_PER_PAGE,
    MIN_LINE_CONFIDENCE,
    boxes_overlap,
    covers_entire_line,
    is_acceptable_size,
    is_chrome_span,
    is_confident,
    is_locatable,
    is_stopword_only,
)
from app.services.text_occlusion.spans import (
    boxes_for_span,
    exact_occurrence_groups,
    expand_exact_leakage,
    locate_phrase,
    ref_for_words,
    span_text,
    words_for_ref,
)


def make_line(
    text: str, top: float = 0.1, confidence: float = 1.0, height: float = 0.04
) -> OcrItem:
    """An `OcrItem` whose word boxes tile the line left-to-right, no overlaps."""
    words: list[OcrWord] = []
    tokens = text.split(" ")
    slot = 1.0 / max(len(tokens), 1)
    for index, token in enumerate(tokens):
        if token:
            words.append(
                OcrWord(
                    text=token,
                    box=Box(
                        left=index * slot,
                        top=top,
                        width=slot * 0.9,
                        height=height,
                    ),
                )
            )
    return OcrItem(
        text=text,
        box=Box(left=0.0, top=top, width=1.0, height=height),
        confidence=confidence,
        words=words,
    )


def make_page(*texts: str, confidence: float = 1.0, spacing: float = 0.05) -> list[OcrItem]:
    return [
        make_line(text, top=0.1 + index * spacing, confidence=confidence)
        for index, text in enumerate(texts)
    ]


SENTENCE = "The renal corpuscle filters blood inside the nephron unit"


def ref_for(line_index: int, phrase: str) -> SpanRef:
    return SpanRef(line_index=line_index, text=phrase)


def model_ref_for(line_index: int, phrase: str) -> ModelSpanRef:
    return ModelSpanRef(line_index=line_index, text=phrase)


def span_for(line_index: int, phrase: str, page_number: int = 1) -> SelectedSpan:
    return SelectedSpan(page_number=page_number, refs=[ref_for(line_index, phrase)])


def candidate_for(line_index: int, phrase: str, page_number: int = 1) -> CandidateSpan:
    return CandidateSpan(page_number=page_number, refs=[model_ref_for(line_index, phrase)])


# --- locate_phrase: addressing spans by text --------------------------------


def test_locate_phrase_finds_a_single_word() -> None:
    line = make_line(SENTENCE)
    located = locate_phrase(line, "renal")

    assert located is not None
    assert (located.start, located.count) == (1, 1)


def test_locate_phrase_finds_a_multiword_run() -> None:
    line = make_line(SENTENCE)
    located = locate_phrase(line, "renal corpuscle")

    assert located is not None
    assert (located.start, located.count) == (1, 2)


def test_locate_phrase_returns_none_when_absent() -> None:
    line = make_line(SENTENCE)
    assert locate_phrase(line, "glomerular filtration") is None
    assert locate_phrase(line, "") is None


def test_locate_phrase_folds_trailing_punctuation() -> None:
    """`System;` and `System` fold alike, so the located run covers the whole
    word rather than stopping short of it."""
    line = make_line("Module 5: Cardiovascular System; an overview for students")
    located = locate_phrase(line, "Cardiovascular System")

    assert located is not None
    words = line.words[located.start : located.start + located.count]
    assert [word.text for word in words] == ["Cardiovascular", "System;"]


def test_locate_phrase_tolerates_a_hyphenated_word_split_across_tokens() -> None:
    """Vision sometimes splits a hyphenated term like `Frank-Starling` across
    two OCR words on the same line; the fold-and-accumulate match still finds
    the run because the folded concatenation ignores the hyphen entirely."""
    line = make_line("the Frank- Starling mechanism explains cardiac output")
    located = locate_phrase(line, "Frank-Starling")

    assert located is not None
    assert located.count == 2


# --- ref resolution ----------------------------------------------------------


def test_words_for_ref_resolves_a_located_phrase() -> None:
    line = make_line(SENTENCE)
    ref = ref_for(0, "renal corpuscle")

    assert [word.text for word in words_for_ref(line, ref)] == ["renal", "corpuscle"]


def test_words_for_ref_resolves_to_nothing_when_unlocatable() -> None:
    line = make_line(SENTENCE)
    ref = ref_for(0, "not on this line at all")

    assert words_for_ref(line, ref) == []


def test_span_text_snaps_to_whole_words() -> None:
    line = make_line(SENTENCE)
    span = span_for(0, "renal corpuscle")

    assert span_text([line], span) == "renal corpuscle"


def test_span_text_joins_across_wrapped_lines() -> None:
    first = make_line("blood enters the renal", top=0.10)
    second = make_line("corpuscle before it is filtered", top=0.20)
    span = SelectedSpan(
        page_number=1,
        refs=[ref_for(0, "renal"), ref_for(1, "corpuscle")],
    )

    assert span_text([first, second], span) == "renal corpuscle"


def test_ref_for_words_sets_every_field() -> None:
    line = make_line(SENTENCE)
    ref = ref_for_words(line.words, line_index=2, start=1, count=2)

    assert ref.line_index == 2
    assert ref.text == "renal corpuscle"
    assert ref.word_start == 1
    assert ref.word_count == 2


def test_span_ref_requires_both_deterministic_word_offsets() -> None:
    with pytest.raises(ValueError, match="provided together"):
        SpanRef(line_index=0, text="renal", word_start=1)


def test_anchored_refs_distinguish_identical_phrases_on_one_line() -> None:
    line = make_line("preload rises while preload falls")
    second = ref_for_words(line.words, line_index=0, start=3, count=1)

    assert [word.text for word in words_for_ref(line, second)] == ["preload"]


# --- deterministic exact-answer closure -------------------------------------


def test_exact_occurrences_find_repetitions_on_the_same_line() -> None:
    line = make_line("Law of the Heart defines output and law of the heart predicts filling")
    groups = exact_occurrence_groups([line], [ref_for(0, "Law of the Heart")])

    assert len(groups) == 2
    assert [group[0].word_start for group in groups] == [0, 7]


def test_exact_occurrences_fold_punctuation_case_and_hyphen_splitting() -> None:
    lines = make_page(
        "Length-dependent activation explains force",
        "The LENGTH- dependent activation mechanism remains active",
    )
    groups = exact_occurrence_groups(
        lines, [ref_for(0, "Length-dependent activation")]
    )

    assert len(groups) == 2
    assert groups[1][0].word_count == 3


def test_exact_occurrences_find_a_wrapped_repetition() -> None:
    lines = make_page(
        "The Frank-Starling mechanism explains output",
        "Recall the Frank-Starling",
        "mechanism during increased filling",
    )
    groups = exact_occurrence_groups(lines, [ref_for(0, "Frank-Starling mechanism")])

    assert len(groups) == 2
    assert [ref.line_index for ref in groups[1]] == [1, 2]


def test_expand_exact_leakage_masks_answer_repeats_and_every_alias_repeat() -> None:
    lines = make_page(
        "Preload determines stroke volume",
        "As preload or end-diastolic volume rises output increases",
        "End-diastolic volume is also called filling volume",
    )
    span = span_for(0, "Preload")
    expanded = expand_exact_leakage(
        lines,
        span,
        alias_groups=[[ref_for(1, "end-diastolic volume")]],
    )

    assert expanded is not None
    assert span_text(lines, expanded) == "Preload"
    assert len(expanded.leakage_refs) == 3
    assert all(ref.word_start is not None for ref in expanded.leakage_refs)


# --- geometry -----------------------------------------------------------------


def test_boxes_for_span_unions_the_covered_words() -> None:
    line = make_line(SENTENCE)
    span = span_for(0, "renal corpuscle")
    boxes = boxes_for_span([line], span)

    assert boxes is not None and len(boxes) == 1
    box = boxes[0]
    renal, corpuscle = line.words[1], line.words[2]
    assert box.left == pytest.approx(renal.box.left)
    assert box.left + box.width == pytest.approx(corpuscle.box.left + corpuscle.box.width)


def test_boxes_for_span_yields_one_box_per_ref() -> None:
    first = make_line("blood enters the renal", top=0.10)
    second = make_line("corpuscle before it is filtered", top=0.20)
    span = SelectedSpan(
        page_number=1,
        refs=[ref_for(0, "renal"), ref_for(1, "corpuscle")],
    )

    boxes = boxes_for_span([first, second], span)

    assert boxes is not None and len(boxes) == 2
    assert boxes[0].top == pytest.approx(0.10)
    assert boxes[1].top == pytest.approx(0.20)


def test_boxes_for_span_is_none_when_a_ref_resolves_to_nothing() -> None:
    line = make_line(SENTENCE)
    span = span_for(0, "not on this line at all")

    assert boxes_for_span([line], span) is None


def test_boxes_for_span_is_none_for_an_out_of_range_line_index() -> None:
    line = make_line(SENTENCE)
    span = span_for(9, "renal corpuscle")

    assert boxes_for_span([line], span) is None


# --- document_context: chrome detection --------------------------------------


def test_chrome_line_repeated_on_enough_pages_is_chrome() -> None:
    line_text = "Prof. M. Andrews (c) 2026"
    pages = [[make_line(line_text)], [make_line(line_text)]]

    chrome = detect_chrome_lines(pages)

    assert filters_module.normalize_text(line_text) in chrome


def test_chrome_line_on_a_single_page_is_not_chrome() -> None:
    pages = [[make_line("Prof. M. Andrews (c) 2026")], [make_line("Unrelated slide content")]]

    chrome = detect_chrome_lines(pages)

    assert filters_module.normalize_text("Unrelated slide content") not in chrome
    assert filters_module.normalize_text("Prof. M. Andrews (c) 2026") not in chrome


def test_long_recurring_sentence_is_not_chrome() -> None:
    """A content sentence that recurs on every page must survive — only SHORT
    recurring lines are furniture, per `MAX_CHROME_LINE_WORDS`."""
    sentence = "The Frank Starling mechanism explains changes in cardiac output daily"
    assert len(sentence.split()) > MAX_CHROME_LINE_WORDS
    pages = [[make_line(sentence)], [make_line(sentence)]]

    chrome = detect_chrome_lines(pages)

    assert filters_module.normalize_text(sentence) not in chrome


def test_a_line_repeated_twice_on_one_page_counts_once() -> None:
    """Repetition must be counted per page, not per occurrence, so a header
    repeated twice on one slide does not masquerade as two pages of repeats."""
    line_text = "Header text here"
    pages = [[make_line(line_text), make_line(line_text)]]

    chrome = detect_chrome_lines(pages)

    assert filters_module.normalize_text(line_text) not in chrome


def test_is_chrome_catches_bare_page_numbers_and_dates() -> None:
    assert is_chrome("3", frozenset()) is True
    assert is_chrome("03/08/2026", frozenset()) is True


def test_is_chrome_does_not_catch_a_decimal_value() -> None:
    assert is_chrome("2.4", frozenset()) is False


def test_is_chrome_checks_membership_in_chrome_lines() -> None:
    chrome_lines = frozenset({filters_module.normalize_text("Copyright Notice")})
    assert is_chrome("Copyright Notice", chrome_lines) is True
    assert is_chrome("Unrelated text", chrome_lines) is False


# --- filters.accept_spans: the slim gate -------------------------------------


def test_low_confidence_line_is_dropped() -> None:
    line = make_line(SENTENCE, confidence=0.3)
    span = span_for(0, "renal corpuscle")

    assert not is_confident([line], span)
    assert accept_spans([line], [span]) == []


def test_confidence_at_the_floor_is_kept() -> None:
    good_line = make_line(SENTENCE, confidence=MIN_LINE_CONFIDENCE)
    span = span_for(0, "renal corpuscle")

    accepted = accept_spans([good_line], [span])

    assert [item.answer for item in accepted] == ["renal corpuscle"]


def test_all_chrome_span_is_dropped() -> None:
    chrome_text = "Prof. M. Andrews (c) 2026"
    line = make_line(chrome_text)
    chrome_lines = frozenset({filters_module.normalize_text(chrome_text)})
    span = span_for(0, "Andrews")

    assert is_chrome_span([line], span, chrome_lines)
    assert accept_spans([line], [span], chrome_lines) == []


def test_unlocatable_phrase_is_dropped() -> None:
    line = make_line(SENTENCE)
    span = span_for(0, "not on this line at all")

    assert not is_locatable([line], span)
    assert accept_spans([line], [span]) == []


def test_oversize_span_is_dropped() -> None:
    line = make_line("one two three four five six seven eight nine ten eleven twelve thirteen end")
    span = span_for(0, "one two three four five six seven eight nine ten eleven twelve thirteen")

    assert not is_acceptable_size(span_text([line], span))
    assert accept_spans([line], [span]) == []


def test_undersize_span_is_dropped() -> None:
    line = make_line("an ox ran up")
    span = span_for(0, "an")

    assert not is_acceptable_size(span_text([line], span))
    assert accept_spans([line], [span]) == []


def test_stopword_only_span_is_dropped() -> None:
    line = make_line("filtration happens in the nephron of the kidney")
    span = span_for(0, "of the")

    assert is_stopword_only(span_text([line], span))
    assert accept_spans([line], [span]) == []


def test_whole_line_span_is_dropped() -> None:
    line = make_line("alpha beta gamma delta epsilon")
    span = span_for(0, "alpha beta gamma delta epsilon")

    assert covers_entire_line([line], span)
    assert accept_spans([line], [span]) == []


def test_overlapping_second_span_is_dropped() -> None:
    line = make_line(SENTENCE)
    first = span_for(0, "renal corpuscle")
    overlapping = span_for(0, "corpuscle filters")

    accepted = accept_spans([line], [first, overlapping])

    assert [item.answer for item in accepted] == ["renal corpuscle"]
    assert boxes_overlap(
        Box(left=0.0, top=0.0, width=0.5, height=0.5),
        Box(left=0.4, top=0.4, width=0.5, height=0.5),
    )
    assert not boxes_overlap(
        Box(left=0.0, top=0.0, width=0.4, height=0.4),
        Box(left=0.5, top=0.5, width=0.4, height=0.4),
    )


def test_duplicate_answer_on_a_page_is_merged_into_one_mask() -> None:
    first, second = make_page(
        "the renal corpuscle filters blood here",
        "the Renal, corpuscle filters blood again",
    )
    spans = [span_for(0, "renal corpuscle"), span_for(1, "Renal, corpuscle")]

    accepted = accept_spans([first, second], spans)

    assert [item.answer for item in accepted] == ["renal corpuscle"]
    assert len(accepted[0].boxes) == 2


def test_leakage_refs_hide_repeated_answer_without_changing_answer_text() -> None:
    first, second = make_page(
        "Starling enunciated the Law of the Heart",
        "Starling's Law of the Heart relates output to filling",
    )
    span = SelectedSpan(
        page_number=1,
        refs=[ref_for(0, "Law of the Heart")],
        leakage_refs=[ref_for(1, "Starling's Law of the Heart")],
    )

    accepted = accept_spans([first, second], [span])

    assert accepted[0].answer == "Law of the Heart"
    assert len(accepted[0].boxes) == 2


def test_max_spans_per_page_cap_is_honoured() -> None:
    # Thin, closely-stacked lines so more than the cap fit in [0, 1] without
    # their boxes overlapping (overlap would reject spans before the cap does).
    count = MAX_SPANS_PER_PAGE + 5
    lines = [
        make_line(
            f"line{index} alpha bravo charlie delta echo foxtrot",
            top=0.02 + index * 0.03,
            height=0.02,
        )
        for index in range(count)
    ]
    spans = [span_for(index, f"line{index} alpha") for index in range(count)]

    accepted = accept_spans(lines, spans)

    assert len(accepted) == MAX_SPANS_PER_PAGE
    assert MAX_SPANS_PER_PAGE < len(spans)


def test_a_clean_span_is_accepted_with_answer_and_boxes() -> None:
    line = make_line(SENTENCE)
    span = span_for(0, "renal corpuscle")

    accepted = accept_spans([line], [span])

    assert len(accepted) == 1
    assert accepted[0] == AcceptedSpan(
        answer="renal corpuscle", boxes=accepted[0].boxes
    )
    assert accepted[0].answer == "renal corpuscle"
    assert accepted[0].boxes


def test_max_span_words_allows_complete_long_terms() -> None:
    assert MAX_SPAN_WORDS == 12


# --- MockTextSpanSelector -----------------------------------------------------


def test_mock_selector_returns_a_flat_list_with_correct_page_numbers() -> None:
    page1 = TextPage(page_number=1, image_path=Path("unused-1.png"), lines=make_page(SENTENCE))
    page2 = TextPage(
        page_number=2,
        image_path=Path("unused-2.png"),
        lines=make_page("glomerular capillaries filter the blood plasma"),
    )

    spans = MockTextSpanSelector().select([page1, page2])

    assert spans, "expected at least one span across the two pages"
    page_numbers = {span.page_number for span in spans}
    assert page_numbers <= {1, 2}


def test_mock_selector_skips_low_confidence_lines() -> None:
    page = TextPage(
        page_number=1,
        image_path=Path("unused.png"),
        lines=[make_line(SENTENCE, confidence=0.3)],
    )

    spans = MockTextSpanSelector().select([page])

    assert spans == []


def test_mock_selector_skips_chrome_lines() -> None:
    chrome_text = "Prof. M. Andrews (c) 2026"
    page = TextPage(
        page_number=1, image_path=Path("unused.png"), lines=[make_line(chrome_text)]
    )
    chrome_lines = frozenset({filters_module.normalize_text(chrome_text)})

    spans = MockTextSpanSelector().select([page], chrome_lines)

    assert spans == []


def test_mock_selector_is_deterministic() -> None:
    page = TextPage(page_number=1, image_path=Path("unused.png"), lines=make_page(SENTENCE))

    first = MockTextSpanSelector().select([page])
    second = MockTextSpanSelector().select([page])

    assert [span.model_dump() for span in first] == [span.model_dump() for span in second]


def test_mock_selector_produces_accepted_spans_through_the_filter_gate() -> None:
    lines = make_page(SENTENCE, "glomerular capillaries filter the blood plasma")
    page = TextPage(page_number=1, image_path=Path("unused.png"), lines=lines)

    spans = MockTextSpanSelector().select([page])
    accepted = accept_spans(lines, spans)

    assert accepted, "the mock selector must yield at least one acceptable span"
    assert accepted[0].boxes


# --- factory ------------------------------------------------------------------


def test_factory_defaults_to_the_mock_selector() -> None:
    selector = get_text_span_selector(get_settings())
    assert isinstance(selector, MockTextSpanSelector)


def test_factory_returns_anthropic_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "anthropic")
    selector = get_text_span_selector(get_settings())
    assert isinstance(selector, AnthropicTextSpanSelector)


def test_factory_rejects_an_unknown_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "nonsense")
    with pytest.raises(ValueError, match="TEXT_OCCLUSION"):
        get_text_span_selector(get_settings())


# --- select_text_occlusions ---------------------------------------------------


class _StubOcr:
    """Deterministic OCR stand-in: each page's lines are supplied up front."""

    def __init__(self, pages: dict[Path, list[OcrItem]]) -> None:
        self._pages = pages
        self.extracted: list[Path] = []

    def extract(self, image_path: Path) -> list[OcrItem]:
        self.extracted.append(image_path)
        return self._pages.get(image_path, [])


def _make_page_image(image_dir: Path, page_number: int) -> Path:
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / f"page_{page_number}.png"
    Image.new("RGB", (200, 150), "white").save(path)
    return path


def test_select_text_occlusions_maps_spans_to_the_right_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "mock")
    image_dir = tmp_path / "pages"
    page1 = _make_page_image(image_dir, 1)
    page2 = _make_page_image(image_dir, 2)
    ocr = _StubOcr(
        {
            page1: make_page(SENTENCE),
            page2: make_page("glomerular capillaries filter the blood plasma today"),
        }
    )
    progress = MagicMock()

    pending = select_text_occlusions(
        get_settings(), image_dir, 2, progress, ocr, ["", ""]
    )

    assert all(isinstance(item, PendingOcclusion) for item in pending)
    assert pending, "expected at least one pending text occlusion"
    assert {item.page_number for item in pending} <= {1, 2}
    for item in pending:
        assert item.page_image == image_dir / f"page_{item.page_number}.png"


def test_select_text_occlusions_advances_progress_once_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "mock")
    image_dir = tmp_path / "pages"
    for page_number in (1, 2, 3):
        _make_page_image(image_dir, page_number)
    ocr = _StubOcr({})
    progress = MagicMock()

    select_text_occlusions(
        get_settings(), image_dir, 3, progress, ocr, ["", "", ""]
    )

    assert progress.advance.call_count == 3


def test_select_text_occlusions_preserves_repeated_answers_across_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "mock")
    image_dir = tmp_path / "pages"
    page1 = _make_page_image(image_dir, 1)
    page2 = _make_page_image(image_dir, 2)
    ocr = _StubOcr(
        {
            page1: make_page("the renal corpuscle filters blood here"),
            page2: make_page("the Renal, corpuscle filters blood indeed"),
        }
    )
    progress = MagicMock()

    pending = select_text_occlusions(
        get_settings(), image_dir, 2, progress, ocr, ["", ""]
    )

    assert [item.page_number for item in pending] == [1, 2]
    assert [item.occlusion.labels[0] for item in pending] == [
        "renal corpuscle",
        "Renal, corpuscle",
    ]


def test_select_text_occlusions_skips_a_missing_page_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "mock")
    image_dir = tmp_path / "pages"
    _make_page_image(image_dir, 1)
    # page 2's image is never created.
    ocr = _StubOcr({})
    progress = MagicMock()

    pending = select_text_occlusions(
        get_settings(), image_dir, 2, progress, ocr, ["", ""]
    )

    assert all(item.page_number == 1 for item in pending)
    assert progress.advance.call_count == 2


# --- shortlist_diagram_pages ---------------------------------------------------


def test_shortlist_excludes_a_dense_prose_page(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "pages"
    page = _make_page_image(image_dir, 1)
    dense_lines = [
        make_line(f"line {index} has quite a few words on it indeed yes", top=0.01 * index)
        for index in range(20)
    ]
    ocr = _StubOcr({page: dense_lines})

    candidates = shortlist_diagram_pages(image_dir, 1, ocr)

    assert candidates == []


def test_shortlist_includes_a_sparse_label_page(tmp_path: Path) -> None:
    image_dir = tmp_path / "pages"
    page = _make_page_image(image_dir, 1)
    sparse_lines = [make_line("Aorta"), make_line("Ventricle", top=0.2)]
    ocr = _StubOcr({page: sparse_lines})

    candidates = shortlist_diagram_pages(image_dir, 1, ocr)

    assert candidates == [1]


def test_shortlist_excludes_a_page_with_no_ocr_text(tmp_path: Path) -> None:
    image_dir = tmp_path / "pages"
    page = _make_page_image(image_dir, 1)
    ocr = _StubOcr({page: []})

    candidates = shortlist_diagram_pages(image_dir, 1, ocr)

    assert candidates == []


def test_shortlist_includes_a_page_with_no_confident_lines(tmp_path: Path) -> None:
    image_dir = tmp_path / "pages"
    page = _make_page_image(image_dir, 1)
    low_confidence_lines = [make_line("garbled handwriting here", confidence=0.2)]
    ocr = _StubOcr({page: low_confidence_lines})

    candidates = shortlist_diagram_pages(image_dir, 1, ocr)

    assert candidates == [1]


def test_shortlist_skips_a_missing_page_image(tmp_path: Path) -> None:
    image_dir = tmp_path / "pages"
    image_dir.mkdir(parents=True)
    ocr = _StubOcr({})

    assert shortlist_diagram_pages(image_dir, 1, ocr) == []


# --- AnthropicTextSpanSelector (no network) -----------------------------------


class _FakeParseResponse:
    def __init__(self, parsed_output: Any | None) -> None:
        self.parsed_output = parsed_output
        self.usage = MagicMock(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )


def _capture_parse(captured: dict[str, Any], result: Any | None) -> Any:
    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        captured.update(kwargs)
        return _FakeParseResponse(result)

    return fake_parse


def _selector_page(tmp_path: Path, page_number: int, text: str) -> TextPage:
    image_path = _make_page_image(tmp_path / "selector-pages", page_number)
    return TextPage(page_number=page_number, image_path=image_path, lines=[make_line(text)])


def test_anthropic_selector_batches_pages_by_batch_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=1)
    pages = [
        _selector_page(tmp_path, 1, "alpha bravo charlie"),
        _selector_page(tmp_path, 2, "delta echo foxtrot"),
    ]

    calls: list[dict[str, Any]] = []

    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        calls.append(kwargs)
        return _FakeParseResponse(CandidateBatch(spans=[]))

    monkeypatch.setattr(selector._client.messages, "parse", fake_parse)

    selector.select(pages)

    assert len(calls) == 2, "batch_pages=1 must issue one call per page"


def test_anthropic_selector_sends_a_low_resolution_preview_and_exact_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    pages = [_selector_page(tmp_path, 1, "cardiac output rises")]

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, CandidateBatch(spans=[]))
    )

    selector.select(pages)

    content = captured["messages"][0]["content"]
    image_blocks = [block for block in content if block["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/jpeg"
    image_bytes = base64.standard_b64decode(image_blocks[0]["source"]["data"])
    with Image.open(BytesIO(image_bytes)) as preview:
        assert max(preview.size) <= 512
    assert any('[0] "cardiac output rises"' in block.get("text", "") for block in content)
    assert captured["output_format"] is CandidateBatch


def test_anthropic_selector_omits_chrome_and_low_confidence_but_keeps_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chrome_text = "Prof. M. Andrews (c) 2026"
    pages = [
        TextPage(
            page_number=1,
            image_path=_make_page_image(tmp_path / "selector-pages", 1),
            lines=[
                make_line("Title Slide", top=0.05),
                make_line(chrome_text, top=0.5),
                make_line("garbled", top=0.6, confidence=0.1),
                make_line("cardiac output rises", top=0.9),
            ],
        )
    ]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    chrome_lines = frozenset({filters_module.normalize_text(chrome_text)})

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, CandidateBatch(spans=[]))
    )

    selector.select(pages, chrome_lines)

    content = captured["messages"][0]["content"]
    listing = next(
        block["text"]
        for block in content
        if '[0] "Title Slide"' in block.get("text", "")
    )
    assert '[0] "Title Slide"' in listing
    assert '[3] "cardiac output rises"' in listing
    assert "[1]" not in listing
    assert "[2]" not in listing


def test_anthropic_selector_flattens_spans_across_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=1)
    pages = [
        _selector_page(tmp_path, 1, "alpha bravo charlie"),
        _selector_page(tmp_path, 2, "delta echo foxtrot"),
    ]

    responses = [
        CandidateBatch(spans=[candidate_for(0, "alpha bravo", page_number=1)]),
        BatchAudit(decisions=[AuditDecision(candidate_index=0, keep=True)]),
        CandidateBatch(spans=[candidate_for(0, "delta echo", page_number=2)]),
        BatchAudit(decisions=[AuditDecision(candidate_index=0, keep=True)]),
    ]

    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        return _FakeParseResponse(responses.pop(0))

    monkeypatch.setattr(selector._client.messages, "parse", fake_parse)

    spans = selector.select(pages)

    assert [span.page_number for span in spans] == [1, 2]
    assert [span.refs[0].text for span in spans] == ["alpha bravo", "delta echo"]


def test_anthropic_selector_preserves_successful_batches_when_a_later_batch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=1)
    pages = [
        _selector_page(tmp_path, 1, "alpha bravo charlie"),
        _selector_page(tmp_path, 2, "delta echo foxtrot"),
    ]
    responses = [
        _FakeParseResponse(
            CandidateBatch(spans=[candidate_for(0, "alpha bravo", page_number=1)])
        ),
        _FakeParseResponse(
            BatchAudit(decisions=[AuditDecision(candidate_index=0, keep=True)])
        ),
        _FakeParseResponse(None),
    ]

    monkeypatch.setattr(
        selector._client.messages, "parse", lambda **kwargs: responses.pop(0)
    )

    spans = selector.select(pages)

    assert [span.page_number for span in spans] == [1]


def test_anthropic_audit_is_text_only_and_adds_semantic_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    image_path = _make_page_image(tmp_path / "selector-pages", 1)
    page = TextPage(
        page_number=1,
        image_path=image_path,
        lines=make_page(
            "Preload determines stroke volume",
            "As preload (end-diastolic volume) rises output increases",
        ),
    )
    calls: list[dict[str, Any]] = []

    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        calls.append(kwargs)
        if kwargs["output_format"] is CandidateBatch:
            return _FakeParseResponse(
                CandidateBatch(spans=[candidate_for(0, "Preload")])
            )
        return _FakeParseResponse(
            BatchAudit(
                decisions=[
                    AuditDecision(
                        candidate_index=0,
                        keep=True,
                        additional_leakage=[
                            RefGroup(refs=[model_ref_for(1, "end-diastolic volume")])
                        ],
                    )
                ]
            )
        )

    monkeypatch.setattr(selector._client.messages, "parse", fake_parse)

    spans = selector.select([page])

    assert len(calls) == 2
    audit_call = calls[1]
    assert audit_call["output_format"] is BatchAudit
    assert all(
        block["type"] == "text" for block in audit_call["messages"][0]["content"]
    )
    audit_text = audit_call["messages"][0]["content"][0]["text"]
    assert audit_text.count("[[C0]]") == 2
    assert "end-diastolic volume" in audit_text
    assert len(spans) == 1
    assert span_text(page.lines, spans[0]) == "Preload"
    assert len(spans[0].leakage_refs) == 2


def test_anthropic_auditor_can_drop_a_broad_sentence_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    page = _selector_page(
        tmp_path,
        1,
        "There is evidence that effectiveness is attenuated in failing myocardium",
    )
    responses = [
        CandidateBatch(
            spans=[
                candidate_for(
                    0, "effectiveness is attenuated in failing myocardium"
                )
            ]
        ),
        BatchAudit(decisions=[AuditDecision(candidate_index=0, keep=False)]),
    ]
    monkeypatch.setattr(
        selector._client.messages,
        "parse",
        lambda **kwargs: _FakeParseResponse(responses.pop(0)),
    )

    assert selector.select([page]) == []


def test_anthropic_auditor_drops_a_candidate_with_unlocatable_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    page = _selector_page(tmp_path, 1, "Preload determines stroke volume")
    responses = [
        CandidateBatch(spans=[candidate_for(0, "Preload")]),
        BatchAudit(
            decisions=[
                AuditDecision(
                    candidate_index=0,
                    keep=True,
                    additional_leakage=[
                        RefGroup(refs=[model_ref_for(0, "not actually on the slide")])
                    ],
                )
            ]
        ),
    ]
    monkeypatch.setattr(
        selector._client.messages,
        "parse",
        lambda **kwargs: _FakeParseResponse(responses.pop(0)),
    )

    assert selector.select([page]) == []


def test_anthropic_audit_fails_closed_when_any_candidate_decision_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    page = TextPage(
        page_number=1,
        image_path=_make_page_image(tmp_path / "selector-pages", 1),
        lines=make_page(
            "Preload determines stroke volume",
            "Afterload determines ejection resistance",
        ),
    )
    responses = [
        CandidateBatch(
            spans=[candidate_for(0, "Preload"), candidate_for(1, "Afterload")]
        ),
        BatchAudit(decisions=[AuditDecision(candidate_index=0, keep=True)]),
    ]
    monkeypatch.setattr(
        selector._client.messages,
        "parse",
        lambda **kwargs: _FakeParseResponse(responses.pop(0)),
    )

    assert selector.select([page]) == []


def test_anthropic_selector_rejects_non_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=0)


# --- detect_objectives --------------------------------------------------------

# The real Frank-Starling p3 shape: the heading extracts LAST in the PDF text
# layer, not first, so detection must match the whole page text.
_OBJECTIVES_PAGE_TEXT = """\
1. Define the Frank-Starling mechanism and describe the "Law of the Heart".
2. Explain the length-tension relationship in cardiac muscle.
3. Compare and contrast intrinsic vs. extrinsic mechanisms of contractility.
4. Describe how the Anrep effect influences cardiac output.
5. Interpret the concept of a family of Starling curves.
6. Discuss how the mechanism is altered in systolic heart failure.

Learning Objectives
"""

# A numbered content list that must NOT be mistaken for objectives: its items
# are noun phrases, not pedagogical verbs.
_CONTENT_LIST_TEXT = "1. Altered Calcium Handling\n2. Anrep Effect\n3. Beta desensitization\n"


def test_detect_objectives_positive_on_heading_extracted_last() -> None:
    text, pages = detect_objectives([_OBJECTIVES_PAGE_TEXT])

    assert text is not None
    assert "Learning Objectives" in text
    assert pages == frozenset({1})


def test_detect_objectives_positive_on_pedagogical_verb_list_without_heading() -> None:
    text, pages = detect_objectives(
        ["1. Define the term.\n2. Explain the process.\n3. Compare two things."]
    )

    assert text is not None
    assert pages == frozenset({1})


def test_detect_objectives_negative_below_min_item_count() -> None:
    assert MIN_OBJECTIVE_ITEMS >= 2  # the fixture below relies on this
    text, pages = detect_objectives(["1. Define the term."])

    assert text is None
    assert pages == frozenset()


def test_detect_objectives_negative_on_numbered_content_list() -> None:
    text, pages = detect_objectives([_CONTENT_LIST_TEXT])

    assert text is None
    assert pages == frozenset()


def test_detect_objectives_negative_on_empty_text() -> None:
    text, pages = detect_objectives([""])

    assert text is None
    assert pages == frozenset()


def test_detect_objectives_concatenates_multiple_slides_in_page_order() -> None:
    other_verbs_slide = "1. Discuss the topic.\n2. Identify the parts."
    texts = ["Title slide", _OBJECTIVES_PAGE_TEXT, "Content slide", other_verbs_slide]

    text, pages = detect_objectives(texts)

    assert pages == frozenset({2, 4})
    assert text is not None
    assert text.index(_OBJECTIVES_PAGE_TEXT.strip()) < text.index(other_verbs_slide.strip())


# --- selector surfacing of context (no network) ------------------------------


def test_objectives_are_prepended_to_the_user_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [_selector_page(tmp_path, 1, "cardiac output rises")]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, CandidateBatch(spans=[]))
    )

    selector.select(pages, objectives="1. Define the Frank-Starling mechanism.")

    content = captured["messages"][0]["content"]
    assert OBJECTIVES_PREFACE in content[0]["text"]
    assert "1. Define the Frank-Starling mechanism." in content[0]["text"]
    assert any("Page 1" in block.get("text", "") for block in content[1:])


def test_objectives_absent_leaves_the_user_message_listing_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [_selector_page(tmp_path, 1, "cardiac output rises")]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, CandidateBatch(spans=[]))
    )

    selector.select(pages)

    text_blocks = [
        block.get("text", "")
        for block in captured["messages"][0]["content"]
        if block["type"] == "text"
    ]
    assert all(OBJECTIVES_PREFACE not in text for text in text_blocks)
