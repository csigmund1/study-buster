"""Deterministic span location and span -> geometry mapping.

A selector names a span as `(page_number, line_index, text)`. Everything
geometric happens here: the phrase is located among the line's OCR words, and
a ref's box is the union of the word boxes it covers, one box per ref, so a
phrase that wraps across two lines yields two boxes.

`Box` rejects zero/negative extents, so every mapping here returns `None`
rather than constructing a degenerate box (see plan §9).
"""

import unicodedata
from dataclasses import dataclass

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord, union_boxes
from app.services.text_occlusion.schemas import SelectedSpan, SpanRef

#: Longest word run considered when enumerating candidate spans.
MAX_CANDIDATE_WORDS = 5


def _fold(text: str) -> str:
    """The comparison key: NFKC-normalized, lowercased, with every
    non-alphanumeric character dropped.

    Dropping punctuation is what stops a phrase from truncating at a trailing
    comma or period: `System;` and `System` fold alike, so a run match covers the
    whole word instead of stopping one character short of it.
    """
    folded = unicodedata.normalize("NFKC", text).lower()
    return "".join(char for char in folded if char.isalnum())


@dataclass(frozen=True)
class WordRange:
    """A contiguous run of one `OcrItem`'s words, by index."""

    start: int
    count: int


def locate_phrase(line: OcrItem, phrase: str) -> WordRange | None:
    """The contiguous run of `line`'s words spelling `phrase`, or `None`.

    Matching compares the *concatenation* of folded words rather than word
    against word, because a selector reading the OCR text tokenizes it the same
    way Vision did, but `2.4um` and `2.4 um` should still be treated as the same
    phrase, and Vision splits hyphenated terms like `Frank-Starling` across two
    words on some lines. The located range always covers whole words, so a
    phrase that is found can never be masked partially.

    The first occurrence on the line wins. Nothing on the wire distinguishes two
    occurrences of the same phrase, so choosing the first is arbitrary but
    deterministic.
    """
    key = _fold(phrase)
    if not key:
        return None

    folds = [_fold(word.text) for word in line.words]
    for start, head in enumerate(folds):
        if not head:
            continue  # a bullet or stray mark is never the head of a phrase
        accumulated = ""
        for index in range(start, len(folds)):
            accumulated += folds[index]
            if accumulated == key:
                return WordRange(start=start, count=index - start + 1)
            if len(accumulated) > len(key):
                break
    return None


def phrases_match(first: str, second: str) -> bool:
    """Whether `first` and `second` are the same phrase under `locate_phrase`'s fold."""
    return _fold(first) == _fold(second)


def line_for_ref(lines: list[OcrItem], ref: SpanRef) -> OcrItem | None:
    """The referenced line, or `None` when the index is out of range."""
    if 0 <= ref.line_index < len(lines):
        return lines[ref.line_index]
    return None


def words_for_ref(line: OcrItem, ref: SpanRef) -> list[OcrWord]:
    """The whole words this ref names, located by its phrase text.

    An empty list means the ref could not be resolved at all; the span is
    dropped upstream rather than masked partially.
    """
    if ref.text.strip():
        located = locate_phrase(line, ref.text)
        if located is not None:
            return line.words[located.start : located.start + located.count]
    return []


def span_text(lines: list[OcrItem], span: SelectedSpan) -> str:
    """The masked phrase as it reads on the page, joined across wrapped lines.

    Built from the **same whole words `boxes_for_span` masks**, never a raw
    character slice, so the answer and the mask are identical by construction
    and every span snaps to word boundaries.
    """
    parts: list[str] = []
    for ref in span.refs:
        line = line_for_ref(lines, ref)
        if line is None:
            continue
        text = " ".join(word.text for word in words_for_ref(line, ref)).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def boxes_for_span(lines: list[OcrItem], span: SelectedSpan) -> list[Box] | None:
    """One box per ref, or `None` if any ref resolves to no usable geometry."""
    if not span.refs:
        return None
    boxes: list[Box] = []
    for ref in span.refs:
        line = line_for_ref(lines, ref)
        if line is None:
            return None
        words = words_for_ref(line, ref)
        box = union_boxes([word.box for word in words])
        if box is None:
            return None
        boxes.append(box)
    return boxes


def ref_for_words(
    words: list[OcrWord], page_number: int, line_index: int, start: int, count: int
) -> SpanRef:
    """A ref covering `count` words of a line starting at word index `start`."""
    covered = words[start : start + count]
    return SpanRef(
        page_number=page_number,
        line_index=line_index,
        text=" ".join(word.text for word in covered),
    )


def enumerate_candidate_spans(
    lines: list[OcrItem], page_number: int, max_words: int = MAX_CANDIDATE_WORDS
) -> list[SelectedSpan]:
    """Every contiguous 1..`max_words` word run on every line, in reading order.

    Single-line only: a wrapped phrase is a semantic judgement, not something
    enumeration can infer, so multi-ref spans come from the selector.
    """
    candidates: list[SelectedSpan] = []
    for line_index, line in enumerate(lines):
        words = line.words
        for start in range(len(words)):
            for count in range(1, min(max_words, len(words) - start) + 1):
                ref = ref_for_words(words, page_number, line_index, start, count)
                candidates.append(SelectedSpan(refs=[ref], answer=ref.text))
    return candidates
