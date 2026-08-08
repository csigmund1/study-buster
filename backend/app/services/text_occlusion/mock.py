"""Deterministic fixture span selector — zero-cost, no model call, no I/O.

Picks at most one span per line by a fixed rule (the first two-word, then
one-word run that is not a function word), starting after the line's first
word. The deterministic filters in `filters.py` still decide what survives, so
the mock exercises exactly the same path a real selection takes.
"""

from app.services.diagram_detection.ocr import OcrItem
from app.services.text_occlusion.base import TextPage
from app.services.text_occlusion.document_context import is_chrome
from app.services.text_occlusion.filters import (
    MIN_LINE_CONFIDENCE,
    is_acceptable_size,
    is_stopword_only,
)
from app.services.text_occlusion.schemas import SelectedSpan
from app.services.text_occlusion.spans import ref_for_words

#: Word-run lengths tried, in order, when picking a line's span.
_RUN_LENGTHS = (2, 1)
#: Words skipped at the start of a line: the opening word is usually a
#: determiner or the least informative token on the line.
_SKIP_LEADING_WORDS = 1


def _span_for_line(page_number: int, line_index: int, line: OcrItem) -> SelectedSpan | None:
    words = line.words
    for count in _RUN_LENGTHS:
        for start in range(_SKIP_LEADING_WORDS, len(words) - count + 1):
            ref = ref_for_words(words, page_number, line_index, start, count)
            if not is_acceptable_size(ref.text) or is_stopword_only(ref.text):
                continue
            return SelectedSpan(refs=[ref], answer=ref.text)
    return None


class MockTextSpanSelector:
    """Returns a deterministic span per confident, non-chrome OCR line, across
    every page handed in, in reading order."""

    def select(
        self,
        pages: list[TextPage],
        chrome_lines: frozenset[str] = frozenset(),
        objectives: str | None = None,  # parity with the protocol; the mock ignores context
    ) -> list[SelectedSpan]:
        spans: list[SelectedSpan] = []
        for page in pages:
            for line_index, line in enumerate(page.lines):
                if line.confidence < MIN_LINE_CONFIDENCE:
                    continue
                if is_chrome(line.text, chrome_lines):
                    continue
                span = _span_for_line(page.page_number, line_index, line)
                if span is not None:
                    spans.append(span)
        return spans
