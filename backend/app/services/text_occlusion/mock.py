"""Deterministic fixture span selector — zero-cost, no model call, no I/O.

Picks at most one span per line by a fixed rule (the first two-word, then
one-word run that is not a function word), starting after the line's first word.
The deterministic filters in `filters.py` still decide what survives, so the
mock exercises exactly the same path a real selection takes.
"""

from app.services.diagram_detection.ocr import OcrItem
from app.services.text_occlusion.base import TextPage
from app.services.text_occlusion.filters import (
    MIN_LINE_CONFIDENCE,
    is_acceptable_size,
    is_stopword_only,
)
from app.services.text_occlusion.schemas import SelectedSpan, SpanSelection
from app.services.text_occlusion.spans import ref_for_words, ref_text

#: Word-run lengths tried, in order, when picking a line's span.
_RUN_LENGTHS = (2, 1)
#: Words skipped at the start of a line: the opening word is usually a
#: determiner or the least informative token on the line.
_SKIP_LEADING_WORDS = 1


def _span_for_line(line_index: int, line: OcrItem) -> SelectedSpan | None:
    words = line.words
    for count in _RUN_LENGTHS:
        for start in range(_SKIP_LEADING_WORDS, len(words) - count + 1):
            ref = ref_for_words(words, line_index, start, count)
            text = ref_text(line, ref)
            if not is_acceptable_size(text) or is_stopword_only(text):
                continue
            return SelectedSpan(refs=[ref], answer=text)
    return None


class MockTextSpanSelector:
    """Returns a deterministic span per confident OCR line, in reading order."""

    def select(self, page: TextPage) -> SpanSelection:
        spans: list[SelectedSpan] = []
        for line_index, line in enumerate(page.lines):
            if line.confidence < MIN_LINE_CONFIDENCE:
                continue
            span = _span_for_line(line_index, line)
            if span is not None:
                spans.append(span)
        return SpanSelection(is_labeled_diagram=bool(page.lines), spans=spans)
