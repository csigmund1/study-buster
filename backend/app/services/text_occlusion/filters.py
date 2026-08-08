"""Deterministic quality gate for selected text spans.

Because the selector now picks phrases from the OCR text it was shown rather
than a page image, every returned span is locatable by construction. What
remains here is cheap correctness/sanity checking, not quality tuning: a
selector may still hallucinate an index, name a confidently-garbled line, or
propose overlapping/duplicate spans across one page's batch, and nothing here
attempts to judge whether a span is a *good* card.
"""

from dataclasses import dataclass

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem
from app.services.draft_validation import normalize_text
from app.services.text_occlusion.document_context import is_chrome
from app.services.text_occlusion.schemas import SelectedSpan
from app.services.text_occlusion.spans import (
    boxes_for_span,
    line_for_ref,
    span_text,
    words_for_ref,
)

#: Vision scores clean printed text ~1.0 and handwritten annotation 0.30-0.50,
#: so this floor is what keeps garbled handwriting out of the masks.
MIN_LINE_CONFIDENCE = 0.5

MIN_SPAN_WORDS = 1
MAX_SPAN_WORDS = 5
MIN_SPAN_CHARS = 3

#: Loose backstop against one page runaway, not a tuned quality knob.
MAX_SPANS_PER_PAGE = 25

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
        "do", "does", "for", "from", "had", "has", "have", "if", "in", "into",
        "is", "it", "its", "may", "not", "of", "on", "or", "that", "the",
        "their", "then", "there", "these", "they", "this", "to", "was", "were",
        "which", "will", "with", "you", "your",
    }
)


@dataclass(frozen=True)
class AcceptedSpan:
    """A span that passed every rule, with the geometry it will mask."""

    answer: str
    boxes: list[Box]


def is_confident(lines: list[OcrItem], span: SelectedSpan) -> bool:
    """True when every line the span touches is above the confidence floor."""
    refs_lines = [line_for_ref(lines, ref) for ref in span.refs]
    if not refs_lines or any(line is None for line in refs_lines):
        return False
    return all(line.confidence >= MIN_LINE_CONFIDENCE for line in refs_lines if line)


def is_chrome_span(
    lines: list[OcrItem], span: SelectedSpan, chrome_lines: frozenset[str]
) -> bool:
    """True when EVERY line the span touches is deck chrome.

    Defence in depth: the prompt already omits chrome lines from the numbered
    list a selector sees, but a selector may still name one — the mock
    proposes a span per line unconditionally, and a model can hallucinate an
    index. A span touching a mix of chrome and content lines is judged on its
    content lines only, so it is not rejected here.
    """
    refs_lines = [line_for_ref(lines, ref) for ref in span.refs]
    if not refs_lines or any(line is None for line in refs_lines):
        return False
    return all(is_chrome(line.text, chrome_lines) for line in refs_lines if line)


def is_locatable(lines: list[OcrItem], span: SelectedSpan) -> bool:
    """True when every ref of the span resolves to at least one OCR word.

    A ref resolves by locating its phrase among the line's words
    (`spans.words_for_ref`). Failing means the selector named something that is
    not on the line — a garbled transcription — and the honest response is to
    drop the span rather than mask an approximation of it.
    """
    for ref in span.refs:
        line = line_for_ref(lines, ref)
        if line is None or not words_for_ref(line, ref):
            return False
    return bool(span.refs)


def word_count(text: str) -> int:
    return len(text.split())


def is_acceptable_size(text: str) -> bool:
    """1-5 words, at least 3 non-space characters."""
    words = word_count(text)
    if not MIN_SPAN_WORDS <= words <= MAX_SPAN_WORDS:
        return False
    return len(text.replace(" ", "")) >= MIN_SPAN_CHARS


def covers_entire_line(lines: list[OcrItem], span: SelectedSpan) -> bool:
    """True when the span masks every word of every line it touches."""
    covered = False
    for ref in span.refs:
        line = line_for_ref(lines, ref)
        if line is None or not line.words:
            return False
        if len(words_for_ref(line, ref)) < len(line.words):
            return False
        covered = True
    return covered


def is_stopword_only(text: str) -> bool:
    """True when every word of the span is a function word."""
    words = normalize_text(text).split()
    if not words:
        return True
    return all(word in STOPWORDS for word in words)


def boxes_overlap(first: Box, second: Box) -> bool:
    """True when two page-normalized boxes share any area."""
    horizontal = min(first.left + first.width, second.left + second.width) - max(
        first.left, second.left
    )
    vertical = min(first.top + first.height, second.top + second.height) - max(
        first.top, second.top
    )
    return horizontal > 0 and vertical > 0


def _overlaps_accepted(boxes: list[Box], accepted: list[AcceptedSpan]) -> bool:
    return any(
        boxes_overlap(box, taken)
        for box in boxes
        for span in accepted
        for taken in span.boxes
    )


def accept_spans(
    lines: list[OcrItem],
    spans: list[SelectedSpan],
    chrome_lines: frozenset[str] = frozenset(),
) -> list[AcceptedSpan]:
    """Apply every rule, in order, returning the spans worth making cards from.

    Order matters only for the page-scoped rules (overlap, dedup, cap): earlier
    spans win, so a selector's own ordering is its preference ordering. A page
    that yields no acceptable span simply produces no card.
    """
    accepted: list[AcceptedSpan] = []
    seen: set[str] = set()
    for span in spans:
        if len(accepted) >= MAX_SPANS_PER_PAGE:
            break
        if not is_confident(lines, span):
            continue
        if is_chrome_span(lines, span, chrome_lines):
            continue
        if not is_locatable(lines, span):
            continue
        text = span_text(lines, span)
        if not is_acceptable_size(text):
            continue
        if is_stopword_only(text):
            continue
        if covers_entire_line(lines, span):
            continue
        boxes = boxes_for_span(lines, span)
        if boxes is None:
            continue
        if _overlaps_accepted(boxes, accepted):
            continue
        key = normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        accepted.append(AcceptedSpan(answer=text, boxes=boxes))
    return accepted
