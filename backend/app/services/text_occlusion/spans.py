"""Deterministic span enumeration and span -> geometry mapping.

The model (or the mock) only ever names a span as `(line_index, char_start,
char_length)`. Everything geometric happens here: a ref's box is the union of
the OCR word boxes it fully covers, one box per ref, so a phrase that wraps
across two lines yields two boxes.

`Box` rejects zero/negative extents, so every mapping here returns `None` rather
than constructing a degenerate box (see plan §9).
"""

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem, OcrWord, union_boxes
from app.services.text_occlusion.schemas import SelectedSpan, SpanRef

#: Longest word run considered when enumerating candidate spans.
MAX_CANDIDATE_WORDS = 5


def line_for_ref(lines: list[OcrItem], ref: SpanRef) -> OcrItem | None:
    """The referenced line, or `None` when the index is out of range."""
    if 0 <= ref.line_index < len(lines):
        return lines[ref.line_index]
    return None


def words_for_ref(line: OcrItem, ref: SpanRef) -> list[OcrWord]:
    """Every word of `line` lying wholly inside the ref's character range."""
    start = ref.char_start
    end = ref.char_start + ref.char_length
    return [
        word
        for word in line.words
        if word.char_start >= start and word.char_start + word.char_length <= end
    ]


def ref_text(line: OcrItem, ref: SpanRef) -> str:
    """The referenced characters of `line`, clamped to the line's own text."""
    start = max(0, ref.char_start)
    end = max(start, ref.char_start + ref.char_length)
    return line.text[start:end].strip()


def span_text(lines: list[OcrItem], span: SelectedSpan) -> str:
    """The masked phrase as it reads on the page, joined across wrapped lines.

    Built from the **same whole words `boxes_for_span` masks**, never a raw
    character slice. A selector may hand back a char range that starts or ends
    mid-word; masking covers only whole words, so slicing characters would print
    an answer the image does not actually hide (e.g. 'ansverse transumbilical
    pla' for a mask over 'transumbilical'). Deriving both from `words_for_ref`
    keeps the answer and the mask identical by construction, and snaps every
    span to word boundaries.
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


def ref_for_words(words: list[OcrWord], line_index: int, start: int, count: int) -> SpanRef:
    """A ref covering `count` words of a line starting at word index `start`."""
    first = words[start]
    last = words[start + count - 1]
    char_start = first.char_start
    char_end = last.char_start + last.char_length
    return SpanRef(
        line_index=line_index, char_start=char_start, char_length=char_end - char_start
    )


def enumerate_candidate_spans(
    lines: list[OcrItem], max_words: int = MAX_CANDIDATE_WORDS
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
                ref = ref_for_words(words, line_index, start, count)
                candidates.append(
                    SelectedSpan(refs=[ref], answer=ref_text(line, ref))
                )
    return candidates
