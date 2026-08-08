"""Text-occlusion span selection, filtering, and span -> box geometry.

Selection is batched and text-only: a selector is handed several already-OCR'd
pages and returns phrases addressed by `(page_number, line_index, text)`.
Geometry is derived deterministically from the OCR words a phrase locates.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.config import get_settings
from app.models import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord
from app.services.document_processing import StyledSpan, extract_page_styles
from app.services.occlusion_pipeline import (
    PendingOcclusion,
    select_text_occlusions,
    shortlist_diagram_pages,
)
from app.services.text_occlusion import (
    AcceptedSpan,
    BatchSelection,
    MockTextSpanSelector,
    SelectedSpan,
    SpanRef,
    TextOcclusionError,
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
from app.services.text_occlusion.emphasis import annotate_emphasis
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
    enumerate_candidate_spans,
    locate_phrase,
    phrases_match,
    ref_for_words,
    span_text,
    words_for_ref,
)


def make_line(
    text: str, top: float = 0.1, confidence: float = 1.0, height: float = 0.04
) -> OcrItem:
    """An `OcrItem` whose word boxes tile the line left-to-right, no overlaps."""
    words: list[OcrWord] = []
    char_start = 0
    tokens = text.split(" ")
    slot = 1.0 / max(len(tokens), 1)
    for index, token in enumerate(tokens):
        if token:
            words.append(
                OcrWord(
                    text=token,
                    char_start=char_start,
                    char_length=len(token),
                    box=Box(
                        left=index * slot,
                        top=top,
                        width=slot * 0.9,
                        height=height,
                    ),
                )
            )
        char_start += len(token) + 1
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


def ref_for(line_index: int, phrase: str, page_number: int = 1) -> SpanRef:
    return SpanRef(page_number=page_number, line_index=line_index, text=phrase)


def span_for(line_index: int, phrase: str, page_number: int = 1) -> SelectedSpan:
    return SelectedSpan(refs=[ref_for(line_index, phrase, page_number)], answer=phrase)


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


def test_phrases_match_uses_the_same_fold() -> None:
    assert phrases_match("Cardiovascular System", "Cardiovascular System;")
    assert not phrases_match("renal corpuscle", "renal tubule")


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
        refs=[ref_for(0, "renal"), ref_for(1, "corpuscle")], answer="renal corpuscle"
    )

    assert span_text([first, second], span) == "renal corpuscle"


def test_ref_for_words_sets_every_field() -> None:
    line = make_line(SENTENCE)
    ref = ref_for_words(line.words, page_number=3, line_index=2, start=1, count=2)

    assert ref.page_number == 3
    assert ref.line_index == 2
    assert ref.text == "renal corpuscle"


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
        refs=[ref_for(0, "renal"), ref_for(1, "corpuscle")], answer="renal corpuscle"
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


def test_enumerate_candidate_spans_sets_page_number() -> None:
    line = make_line("alpha beta gamma")
    candidates = enumerate_candidate_spans([line], page_number=5, max_words=2)

    assert candidates, "expected at least one candidate span"
    for candidate in candidates:
        assert all(ref.page_number == 5 for ref in candidate.refs)


def test_enumerate_candidate_spans_covers_word_runs_in_reading_order() -> None:
    line = make_line("alpha beta gamma")
    candidates = enumerate_candidate_spans([line], page_number=1, max_words=2)

    assert [span_text([line], span) for span in candidates] == [
        "alpha",
        "alpha beta",
        "beta",
        "beta gamma",
        "gamma",
    ]


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
    line = make_line(SENTENCE)
    span = span_for(0, "The renal corpuscle filters blood inside")  # 6 words

    assert not is_acceptable_size(span.answer)
    assert accept_spans([line], [span]) == []


def test_undersize_span_is_dropped() -> None:
    line = make_line("an ox ran up")
    span = span_for(0, "an")

    assert not is_acceptable_size(span.answer)
    assert accept_spans([line], [span]) == []


def test_stopword_only_span_is_dropped() -> None:
    line = make_line("filtration happens in the nephron of the kidney")
    span = span_for(0, "of the")

    assert is_stopword_only(span.answer)
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


def test_duplicate_answer_on_a_page_is_dropped() -> None:
    first, second = make_page(
        "the renal corpuscle filters blood here",
        "the Renal, corpuscle filters blood again",
    )
    spans = [span_for(0, "renal corpuscle"), span_for(1, "Renal, corpuscle")]

    accepted = accept_spans([first, second], spans)

    assert [item.answer for item in accepted] == ["renal corpuscle"]


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


def test_max_span_words_constant_is_five() -> None:
    assert MAX_SPAN_WORDS == 5


# --- MockTextSpanSelector -----------------------------------------------------


def test_mock_selector_returns_a_flat_list_with_correct_page_numbers() -> None:
    page1 = TextPage(page_number=1, lines=make_page(SENTENCE))
    page2 = TextPage(
        page_number=2, lines=make_page("glomerular capillaries filter the blood plasma")
    )

    spans = MockTextSpanSelector().select([page1, page2])

    assert spans, "expected at least one span across the two pages"
    page_numbers = {ref.page_number for span in spans for ref in span.refs}
    assert page_numbers <= {1, 2}
    for span in spans:
        assert len({ref.page_number for ref in span.refs}) == 1


def test_mock_selector_skips_low_confidence_lines() -> None:
    page = TextPage(page_number=1, lines=[make_line(SENTENCE, confidence=0.3)])

    spans = MockTextSpanSelector().select([page])

    assert spans == []


def test_mock_selector_skips_chrome_lines() -> None:
    chrome_text = "Prof. M. Andrews (c) 2026"
    page = TextPage(page_number=1, lines=[make_line(chrome_text)])
    chrome_lines = frozenset({filters_module.normalize_text(chrome_text)})

    spans = MockTextSpanSelector().select([page], chrome_lines)

    assert spans == []


def test_mock_selector_is_deterministic() -> None:
    page = TextPage(page_number=1, lines=make_page(SENTENCE))

    first = MockTextSpanSelector().select([page])
    second = MockTextSpanSelector().select([page])

    assert [span.model_dump() for span in first] == [span.model_dump() for span in second]


def test_mock_selector_produces_accepted_spans_through_the_filter_gate() -> None:
    lines = make_page(SENTENCE, "glomerular capillaries filter the blood plasma")
    page = TextPage(page_number=1, lines=lines)

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
        get_settings(), image_dir, 2, progress, ocr, ["", ""], tmp_path / "deck.pdf"
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
        get_settings(), image_dir, 3, progress, ocr, ["", "", ""], tmp_path / "deck.pdf"
    )

    assert progress.advance.call_count == 3


def test_select_text_occlusions_dedupes_answers_across_pages(
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
        get_settings(), image_dir, 2, progress, ocr, ["", ""], tmp_path / "deck.pdf"
    )

    answers = [item.occlusion.labels[0] for item in pending]
    assert len(answers) == len(set(answers)), "cross-page dedup must remove repeated answers"
    # the earliest page keeps its occurrence
    assert 1 in {item.page_number for item in pending}


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
        get_settings(), image_dir, 2, progress, ocr, ["", ""], tmp_path / "deck.pdf"
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
    def __init__(self, parsed_output: BatchSelection | None) -> None:
        self.parsed_output = parsed_output


def _capture_parse(captured: dict[str, Any], result: BatchSelection | None) -> Any:
    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        captured.update(kwargs)
        return _FakeParseResponse(result)

    return fake_parse


def test_anthropic_selector_batches_pages_by_batch_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=1)
    pages = [
        TextPage(page_number=1, lines=[make_line("alpha bravo charlie")]),
        TextPage(page_number=2, lines=[make_line("delta echo foxtrot")]),
    ]

    calls: list[dict[str, Any]] = []

    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        calls.append(kwargs)
        return _FakeParseResponse(BatchSelection(spans=[]))

    monkeypatch.setattr(selector._client.messages, "parse", fake_parse)

    selector.select(pages)

    assert len(calls) == 2, "batch_pages=1 must issue one call per page"


def test_anthropic_selector_sends_a_text_only_message(monkeypatch: pytest.MonkeyPatch) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    pages = [TextPage(page_number=1, lines=[make_line("cardiac output rises")])]

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, BatchSelection(spans=[]))
    )

    selector.select(pages)

    content = captured["messages"][0]["content"]
    assert all(block["type"] == "text" for block in content), "no image block must be sent"
    assert any('[0] "cardiac output rises"' in block["text"] for block in content)


def test_anthropic_selector_omits_chrome_and_low_confidence_but_keeps_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chrome_text = "Prof. M. Andrews (c) 2026"
    pages = [
        TextPage(
            page_number=1,
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
        selector._client.messages, "parse", _capture_parse(captured, BatchSelection(spans=[]))
    )

    selector.select(pages, chrome_lines)

    content = captured["messages"][0]["content"]
    listing = next(block["text"] for block in content if "Page 1" in block["text"])
    assert '[0] "Title Slide"' in listing
    assert '[3] "cardiac output rises"' in listing
    assert "[1]" not in listing
    assert "[2]" not in listing


def test_anthropic_selector_flattens_spans_across_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=1)
    pages = [
        TextPage(page_number=1, lines=[make_line("alpha bravo charlie")]),
        TextPage(page_number=2, lines=[make_line("delta echo foxtrot")]),
    ]

    responses = [
        BatchSelection(spans=[span_for(0, "alpha bravo", page_number=1)]),
        BatchSelection(spans=[span_for(0, "delta echo", page_number=2)]),
    ]

    def fake_parse(**kwargs: Any) -> _FakeParseResponse:
        return _FakeParseResponse(responses.pop(0))

    monkeypatch.setattr(selector._client.messages, "parse", fake_parse)

    spans = selector.select(pages)

    assert [span.answer for span in spans] == ["alpha bravo", "delta echo"]


def test_anthropic_selector_raises_when_parsed_output_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)
    pages = [TextPage(page_number=1, lines=[make_line("cardiac output rises")])]

    monkeypatch.setattr(
        selector._client.messages, "parse", lambda **kwargs: _FakeParseResponse(None)
    )

    with pytest.raises(TextOcclusionError):
        selector.select(pages)


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


# --- emphasis: extract_page_styles + annotate_emphasis -----------------------


def test_extract_page_styles_flags_bold_and_leaves_plain_unmarked(tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Frank Starling", fontname="hebo", fontsize=18)  # bold builtin
    page.insert_text((72, 120), "plain caption", fontname="helv", fontsize=18)  # regular
    pdf_path = tmp_path / "deck.pdf"
    doc.save(pdf_path)
    doc.close()

    styles = extract_page_styles(pdf_path)

    assert len(styles) == 1
    bold_text = " ".join(span.text for span in styles[0] if span.bold)
    plain_text = " ".join(span.text for span in styles[0] if not span.bold)
    assert "Frank" in bold_text or "Starling" in bold_text
    assert "plain" in plain_text


def test_annotate_emphasis_marks_a_line_over_a_bold_span() -> None:
    line = make_line("Frank Starling mechanism")  # full-width box at top=0.1
    styled = [
        StyledSpan(
            text="Frank Starling mechanism",
            box=Box(left=0.0, top=0.1, width=0.6, height=0.04),
            bold=True,
            italic=False,
        )
    ]

    result = annotate_emphasis([line], styled)

    assert result[0].emphasized is True


def test_annotate_emphasis_leaves_a_plain_line_unmarked() -> None:
    line = make_line("plain body text here")
    styled = [
        StyledSpan(
            text="plain body text here",
            box=Box(left=0.0, top=0.1, width=0.6, height=0.04),
            bold=False,
            italic=False,
        )
    ]

    result = annotate_emphasis([line], styled)

    assert result[0].emphasized is False


def test_annotate_emphasis_ignores_a_line_with_no_overlapping_span() -> None:
    # Handwriting/annotation: an OCR line with no PDF span beneath it.
    line = make_line("handwritten note", top=0.9)
    styled = [
        StyledSpan(
            text="printed heading",
            box=Box(left=0.0, top=0.1, width=0.6, height=0.04),
            bold=True,
            italic=False,
        )
    ]

    result = annotate_emphasis([line], styled)

    assert result[0].emphasized is False


def test_annotate_emphasis_returns_input_unchanged_without_styled_spans() -> None:
    lines = make_page(SENTENCE)

    result = annotate_emphasis(lines, [])

    assert result is lines
    assert all(not line.emphasized for line in result)


# --- selector surfacing of context (no network) ------------------------------


def test_format_page_marks_emphasized_lines_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emphasized = make_line("cardiac output rises").model_copy(update={"emphasized": True})
    plain = make_line("as noted above", top=0.5)
    pages = [TextPage(page_number=1, lines=[emphasized, plain])]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, BatchSelection(spans=[]))
    )

    selector.select(pages)

    listing = next(
        block["text"] for block in captured["messages"][0]["content"] if "Page 1" in block["text"]
    )
    assert '[0] "cardiac output rises"  [EMPHASIZED]' in listing
    assert '[1] "as noted above"' in listing
    assert '[1] "as noted above"  [EMPHASIZED]' not in listing


def test_objectives_are_prepended_to_the_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [TextPage(page_number=1, lines=[make_line("cardiac output rises")])]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, BatchSelection(spans=[]))
    )

    selector.select(pages, objectives="1. Define the Frank-Starling mechanism.")

    text = captured["messages"][0]["content"][0]["text"]
    assert OBJECTIVES_PREFACE in text
    assert "1. Define the Frank-Starling mechanism." in text
    # objectives lead, then the page listing follows
    assert text.index(OBJECTIVES_PREFACE) < text.index("Page 1")


def test_objectives_absent_leaves_the_user_message_listing_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [TextPage(page_number=1, lines=[make_line("cardiac output rises")])]
    selector = AnthropicTextSpanSelector("claude-haiku-4-5", batch_pages=10)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        selector._client.messages, "parse", _capture_parse(captured, BatchSelection(spans=[]))
    )

    selector.select(pages)

    text = captured["messages"][0]["content"][0]["text"]
    assert OBJECTIVES_PREFACE not in text
