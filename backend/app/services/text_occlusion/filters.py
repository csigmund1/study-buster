"""Deterministic quality gate for selected text spans (plan §5.3).

Every rule here is plain Python and independently testable. A selector may
propose anything; nothing reaches a card without passing all of these. A page
that yields no acceptable span simply produces no card.
"""

from dataclasses import dataclass

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem
from app.services.draft_validation import normalize_text
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

#: Visible words that must remain across the span's line(s) after masking, so
#: the card still has enough context to be answerable.
MIN_REMAINING_WORDS = 4

#: Hard, non-configurable per-page cap. Not a user setting: it exists so one
#: over-eager page cannot emit dozens of near-identical cards.
MAX_SPANS_PER_PAGE = 5

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


def enough_context_remains(lines: list[OcrItem], span: SelectedSpan) -> bool:
    """True when >= `MIN_REMAINING_WORDS` words stay visible on the span's line(s)."""
    masked: dict[int, int] = {}
    totals: dict[int, int] = {}
    for ref in span.refs:
        line = line_for_ref(lines, ref)
        if line is None:
            return False
        totals[ref.line_index] = len(line.words)
        masked[ref.line_index] = masked.get(ref.line_index, 0) + len(words_for_ref(line, ref))
    remaining = sum(totals[index] - min(masked[index], totals[index]) for index in totals)
    return remaining >= MIN_REMAINING_WORDS


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


def accept_spans(lines: list[OcrItem], spans: list[SelectedSpan]) -> list[AcceptedSpan]:
    """Apply every rule, in order, returning the spans worth making cards from.

    Order matters only for the page-scoped rules (overlap, dedup, cap): earlier
    spans win, so a selector's own ordering is its preference ordering.
    """
    accepted: list[AcceptedSpan] = []
    seen: set[str] = set()
    for span in spans:
        if len(accepted) >= MAX_SPANS_PER_PAGE:
            break
        if not is_confident(lines, span):
            continue
        text = span_text(lines, span)
        if not is_acceptable_size(text):
            continue
        if covers_entire_line(lines, span):
            continue
        if not enough_context_remains(lines, span):
            continue
        if is_stopword_only(text):
            continue
        key = normalize_text(text)
        if not key or key in seen:
            continue
        boxes = boxes_for_span(lines, span)
        if boxes is None:
            continue
        if _overlaps_accepted(boxes, accepted):
            continue
        seen.add(key)
        accepted.append(AcceptedSpan(answer=text, boxes=boxes))
    return accepted
