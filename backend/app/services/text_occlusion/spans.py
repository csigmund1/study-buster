"""Deterministic span location and span -> geometry mapping.

A selector names a page once and identifies text within that page by
`(line_index, text)`. This module anchors those phrases to OCR word ranges, finds
every normalized slide-local repetition (including same-line and wrapped
occurrences), and maps every final ref to geometry. The answer's refs and all
exact or audited semantic leakage refs are masked together.

`Box` rejects zero/negative extents, so every mapping here returns `None`
rather than constructing a degenerate box (see plan §9).
"""

import unicodedata
from dataclasses import dataclass

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord, union_boxes
from app.services.text_occlusion.schemas import SelectedSpan, SpanRef

MAX_WRAPPED_OCCURRENCE_LINES = 3


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


def _resolved_range(line: OcrItem, ref: SpanRef) -> WordRange | None:
    """Resolve a model phrase or an internally anchored deterministic ref."""
    if ref.word_start is None or ref.word_count is None:
        return locate_phrase(line, ref.text)
    end = ref.word_start + ref.word_count
    if end > len(line.words):
        return None
    covered = line.words[ref.word_start:end]
    if _fold("".join(word.text for word in covered)) != _fold(ref.text):
        return None
    return WordRange(start=ref.word_start, count=ref.word_count)


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
        located = _resolved_range(line, ref)
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


def all_refs(span: SelectedSpan) -> list[SpanRef]:
    """The answer occurrence followed by every repeated-answer occurrence."""
    return [*span.refs, *span.leakage_refs]


def boxes_for_span(lines: list[OcrItem], span: SelectedSpan) -> list[Box] | None:
    """One box per ref, or `None` if any ref resolves to no usable geometry."""
    if not span.refs:
        return None
    boxes: list[Box] = []
    for ref in all_refs(span):
        line = line_for_ref(lines, ref)
        if line is None:
            return None
        words = words_for_ref(line, ref)
        box = union_boxes([word.box for word in words])
        if box is None:
            return None
        boxes.append(box)
    return boxes


def ref_for_words(words: list[OcrWord], line_index: int, start: int, count: int) -> SpanRef:
    """A ref covering `count` words of a line starting at word index `start`."""
    covered = words[start : start + count]
    return SpanRef(
        line_index=line_index,
        text=" ".join(word.text for word in covered),
        word_start=start,
        word_count=count,
    )


def _range_key(lines: list[OcrItem], ref: SpanRef) -> tuple[int, int, int] | None:
    line = line_for_ref(lines, ref)
    if line is None:
        return None
    located = _resolved_range(line, ref)
    if located is None:
        return None
    return (ref.line_index, located.start, located.count)


def _refs_key(lines: list[OcrItem], refs: list[SpanRef]) -> str | None:
    parts: list[str] = []
    for ref in refs:
        line = line_for_ref(lines, ref)
        if line is None:
            return None
        words = words_for_ref(line, ref)
        if not words:
            return None
        parts.extend(word.text for word in words)
    key = _fold("".join(parts))
    return key or None


def exact_occurrence_groups(
    lines: list[OcrItem], refs: list[SpanRef]
) -> list[list[SpanRef]]:
    """Find every exact normalized occurrence of one ref group on a slide.

    Matching uses the same punctuation/case/hyphen folding as normal ref
    localization. Returned refs are anchored to exact OCR word ranges, which is
    what lets two identical phrases on the same line receive separate masks.
    A wrapped occurrence may consume the suffix of one line and the prefix of
    up to two immediately following OCR lines.
    """
    key = _refs_key(lines, refs)
    if key is None:
        return []

    found: list[list[SpanRef]] = []
    seen: set[tuple[tuple[int, int, int], ...]] = set()
    for start_line, line in enumerate(lines):
        for start_word, word in enumerate(line.words):
            if not _fold(word.text):
                continue
            accumulated = ""
            pieces: list[tuple[int, int, int]] = []
            matched: list[SpanRef] | None = None
            stop = False
            last_line = min(len(lines), start_line + MAX_WRAPPED_OCCURRENCE_LINES)
            for line_index in range(start_line, last_line):
                current = lines[line_index]
                word_index = start_word if line_index == start_line else 0
                while word_index < len(current.words) and not _fold(
                    current.words[word_index].text
                ):
                    word_index += 1
                if word_index >= len(current.words):
                    stop = True
                    break
                piece_start = word_index
                for index in range(word_index, len(current.words)):
                    accumulated += _fold(current.words[index].text)
                    if not key.startswith(accumulated):
                        stop = True
                        break
                    if accumulated == key:
                        ranges = [*pieces, (line_index, piece_start, index - piece_start + 1)]
                        matched = [
                            ref_for_words(lines[li].words, li, start, count)
                            for li, start, count in ranges
                        ]
                        break
                if stop or matched is not None:
                    break
                pieces.append(
                    (line_index, piece_start, len(current.words) - piece_start)
                )

            if matched is None:
                continue
            group_key = tuple(
                key_part
                for ref in matched
                if (key_part := _range_key(lines, ref)) is not None
            )
            if group_key and group_key not in seen:
                seen.add(group_key)
                found.append(matched)
    return found


def expand_exact_leakage(
    lines: list[OcrItem],
    span: SelectedSpan,
    alias_groups: list[list[SpanRef]] | None = None,
) -> SelectedSpan | None:
    """Hide every exact occurrence of the answer and audited aliases.

    The primary occurrence stays in ``refs`` so it remains the card's answer.
    All other anchored occurrences are flattened into ``leakage_refs``. ``None``
    means a supplied primary or alias group could not be resolved honestly.
    """
    sources = [span.refs, *(alias_groups or [])]
    if span.leakage_refs:
        sources.extend([[ref] for ref in span.leakage_refs])

    primary_keys = {_range_key(lines, ref) for ref in span.refs}
    if None in primary_keys or not primary_keys:
        return None

    leakage: list[SpanRef] = []
    seen_ranges = set(primary_keys)
    for source in sources:
        occurrences = exact_occurrence_groups(lines, source)
        if not occurrences:
            return None
        for occurrence in occurrences:
            for ref in occurrence:
                range_key = _range_key(lines, ref)
                if range_key is None:
                    return None
                if range_key in seen_ranges:
                    continue
                seen_ranges.add(range_key)
                leakage.append(ref)
    return SelectedSpan(
        page_number=span.page_number,
        refs=span.refs,
        leakage_refs=leakage,
    )
