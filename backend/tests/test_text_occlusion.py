"""Text-occlusion span selection, filtering, and span -> box geometry."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.models import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord
from app.services.text_occlusion import (
    MockTextSpanSelector,
    SelectedSpan,
    SpanRef,
    TextPage,
    accept_spans,
    get_text_span_selector,
)
from app.services.text_occlusion import filters as filters_module
from app.services.text_occlusion.filters import (
    MAX_SPANS_PER_PAGE,
    MIN_LINE_CONFIDENCE,
    boxes_overlap,
    covers_entire_line,
    enough_context_remains,
    is_acceptable_size,
    is_confident,
    is_stopword_only,
)
from app.services.text_occlusion.spans import (
    boxes_for_span,
    enumerate_candidate_spans,
    span_text,
)


def make_line(text: str, top: float = 0.1, confidence: float = 1.0) -> OcrItem:
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
                        height=0.04,
                    ),
                )
            )
        char_start += len(token) + 1
    return OcrItem(
        text=text,
        box=Box(left=0.0, top=top, width=1.0, height=0.04),
        confidence=confidence,
        words=words,
    )


def ref_for(line: OcrItem, phrase: str, line_index: int = 0) -> SpanRef:
    start = line.text.index(phrase)
    return SpanRef(line_index=line_index, char_start=start, char_length=len(phrase))


def span_for(line: OcrItem, phrase: str, line_index: int = 0) -> SelectedSpan:
    return SelectedSpan(refs=[ref_for(line, phrase, line_index)], answer=phrase)


SENTENCE = "The renal corpuscle filters blood inside the nephron unit"


# --- geometry -------------------------------------------------------------


def test_boxes_for_span_unions_only_the_words_inside_the_range() -> None:
    line = make_line(SENTENCE)
    boxes = boxes_for_span([line], span_for(line, "renal corpuscle"))

    assert boxes is not None and len(boxes) == 1
    box = boxes[0]
    renal, corpuscle = line.words[1], line.words[2]
    assert box.left == pytest.approx(renal.box.left)
    assert box.left + box.width == pytest.approx(corpuscle.box.left + corpuscle.box.width)


def test_wrapped_phrase_yields_one_box_per_line_fragment() -> None:
    first = make_line("blood enters the renal", top=0.10)
    second = make_line("corpuscle before it is filtered", top=0.20)
    span = SelectedSpan(
        refs=[ref_for(first, "renal", 0), ref_for(second, "corpuscle", 1)],
        answer="renal corpuscle",
    )

    boxes = boxes_for_span([first, second], span)

    assert boxes is not None and len(boxes) == 2
    assert boxes[0].top == pytest.approx(0.10)
    assert boxes[1].top == pytest.approx(0.20)
    assert span_text([first, second], span) == "renal corpuscle"


def test_span_with_no_covered_words_is_dropped_not_raised() -> None:
    line = make_line(SENTENCE)
    # A range that starts and ends inside one word covers no whole word.
    span = SelectedSpan(refs=[SpanRef(line_index=0, char_start=5, char_length=2)], answer="en")

    assert boxes_for_span([line], span) is None


def test_span_referencing_an_unknown_line_is_dropped() -> None:
    line = make_line(SENTENCE)
    span = SelectedSpan(refs=[SpanRef(line_index=9, char_start=0, char_length=5)], answer="x")

    assert boxes_for_span([line], span) is None


def test_line_without_word_boxes_yields_no_geometry() -> None:
    line = OcrItem(text=SENTENCE, box=Box(left=0, top=0, width=1, height=0.05))
    span = SelectedSpan(refs=[SpanRef(line_index=0, char_start=0, char_length=3)], answer="The")

    assert boxes_for_span([line], span) is None


def test_enumerate_candidate_spans_covers_word_runs_in_reading_order() -> None:
    line = make_line("alpha beta gamma")
    candidates = enumerate_candidate_spans([line], max_words=2)

    assert [span_text([line], span) for span in candidates] == [
        "alpha",
        "alpha beta",
        "beta",
        "beta gamma",
        "gamma",
    ]


# --- individual filter rules ---------------------------------------------


def test_low_confidence_lines_are_not_masked() -> None:
    line = make_line(SENTENCE, confidence=0.42)
    span = span_for(line, "renal corpuscle")

    assert not is_confident([line], span)
    assert accept_spans([line], [span]) == []


def test_confidence_at_the_floor_is_accepted() -> None:
    line = make_line(SENTENCE, confidence=MIN_LINE_CONFIDENCE)
    assert is_confident([line], span_for(line, "renal corpuscle"))


def test_span_size_rules() -> None:
    assert is_acceptable_size("renal corpuscle")
    assert not is_acceptable_size("an")  # fewer than 3 characters
    assert not is_acceptable_size("")
    assert not is_acceptable_size("one two three four five six")  # more than 5 words


def test_whole_line_spans_are_rejected() -> None:
    line = make_line("alpha beta gamma delta epsilon")
    span = span_for(line, "alpha beta gamma delta epsilon")

    assert covers_entire_line([line], span)
    assert accept_spans([line], [span]) == []


def test_too_little_remaining_context_is_rejected() -> None:
    line = make_line("renal corpuscle filters blood")
    span = span_for(line, "renal corpuscle")

    assert not enough_context_remains([line], span)
    assert accept_spans([line], [span]) == []


def test_enough_remaining_context_is_accepted() -> None:
    line = make_line(SENTENCE)
    assert enough_context_remains([line], span_for(line, "renal corpuscle"))


def test_stopword_only_spans_are_rejected() -> None:
    line = make_line(SENTENCE)
    span = span_for(line, "the nephron")

    assert is_stopword_only("of the")
    assert not is_stopword_only("renal corpuscle")
    assert not is_stopword_only(span.answer)


def test_stopword_only_span_produces_no_card() -> None:
    line = make_line("filtration happens in the nephron of the kidney")
    span = span_for(line, "of the")

    assert accept_spans([line], [span]) == []


def test_overlapping_spans_are_rejected() -> None:
    line = make_line(SENTENCE)
    first = span_for(line, "renal corpuscle")
    overlapping = span_for(line, "corpuscle filters")

    accepted = accept_spans([line], [first, overlapping])

    assert [span.answer for span in accepted] == ["renal corpuscle"]
    assert boxes_overlap(
        Box(left=0.0, top=0.0, width=0.5, height=0.5),
        Box(left=0.4, top=0.4, width=0.5, height=0.5),
    )
    assert not boxes_overlap(
        Box(left=0.0, top=0.0, width=0.4, height=0.4),
        Box(left=0.5, top=0.5, width=0.4, height=0.4),
    )


def test_duplicate_answers_are_deduped_across_lines() -> None:
    first = make_line("the renal corpuscle filters blood here", top=0.1)
    second = make_line("the Renal, corpuscle filters blood again", top=0.3)
    spans = [
        span_for(first, "renal corpuscle", 0),
        span_for(second, "Renal, corpuscle", 1),
    ]

    accepted = accept_spans([first, second], spans)

    assert [span.answer for span in accepted] == ["renal corpuscle"]


def test_per_page_cap_holds() -> None:
    # Distinct phrases per line, so dedup does not do the capping for us.
    lines = [
        make_line(f"the renal corpuscle{index} filters blood inside", top=0.05 * (index + 1))
        for index in range(10)
    ]
    spans = [
        span_for(line, f"renal corpuscle{index}", index) for index, line in enumerate(lines)
    ]

    accepted = accept_spans(lines, spans)

    assert len(accepted) == MAX_SPANS_PER_PAGE
    assert MAX_SPANS_PER_PAGE < len(spans)


def test_page_with_no_acceptable_span_produces_nothing() -> None:
    line = make_line("of the", confidence=0.2)
    assert accept_spans([line], [span_for(line, "of the")]) == []
    assert accept_spans([line], []) == []


def test_cap_constant_is_not_configurable() -> None:
    settings = get_settings()
    assert not hasattr(settings, "max_spans_per_page")
    assert isinstance(filters_module.MAX_SPANS_PER_PAGE, int)


# --- mock selector + factory ---------------------------------------------


def test_mock_selector_produces_an_acceptable_span(tmp_path: Path) -> None:
    line = make_line(SENTENCE)
    page = TextPage(page_number=1, image_path=tmp_path / "page_1.png", lines=[line])

    selection = MockTextSpanSelector().select(page)
    accepted = accept_spans([line], selection.spans)

    assert selection.is_labeled_diagram is True
    assert accepted, "the mock selector must yield at least one acceptable span"
    assert accepted[0].boxes


def test_mock_selector_skips_low_confidence_lines(tmp_path: Path) -> None:
    line = make_line(SENTENCE, confidence=0.3)
    page = TextPage(page_number=1, image_path=tmp_path / "page_1.png", lines=[line])

    selection = MockTextSpanSelector().select(page)

    assert selection.spans == []


def test_mock_selector_is_deterministic(tmp_path: Path) -> None:
    line = make_line(SENTENCE)
    page = TextPage(page_number=1, image_path=tmp_path / "page_1.png", lines=[line])

    first = MockTextSpanSelector().select(page)
    second = MockTextSpanSelector().select(page)

    assert first.model_dump() == second.model_dump()


def test_factory_defaults_to_the_mock_selector() -> None:
    selector = get_text_span_selector(get_settings())
    assert isinstance(selector, MockTextSpanSelector)


def test_factory_rejects_an_unknown_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_OCCLUSION", "nonsense")
    with pytest.raises(ValueError, match="TEXT_OCCLUSION"):
        get_text_span_selector(get_settings())


def test_answer_text_snaps_to_whole_words_not_a_character_slice() -> None:
    """A mid-word char range must not print an answer the mask does not hide.

    Regression from the first real-mode run, which produced answers like
    'ansverse transumbilical pla' for a mask covering only 'transumbilical'.
    """
    from app.services.text_occlusion.spans import boxes_for_span, span_text

    line = make_line("the transverse transumbilical plane divides it", top=0.2)
    # Range starts and ends mid-word, as a model plausibly returns.
    span = SelectedSpan(
        refs=[SpanRef(line_index=0, char_start=6, char_length=25)], answer="ignored"
    )

    text = span_text([line], span)
    boxes = boxes_for_span([line], span)

    assert boxes is not None
    # Answer is exactly the whole words the mask covers — no partial words.
    assert text == "transumbilical"
    assert not text.startswith("ansverse")
    for word in text.split():
        assert word in line.text.split()
