"""Attach the lecturer's bold/italic emphasis onto OCR lines.

Apple Vision OCR carries no font style, so emphasis is recovered from the PDF
font layer (`StyledSpan`, from `document_processing`) and matched onto OCR lines
by page-normalized box overlap. A line counts as emphasized when the bold/italic
share of its overlap area with the page's styled spans clears
`EMPHASIS_MIN_FRACTION` — robust to the OCR and PDF layers tokenizing text
differently, and to handwriting/annotation lines that have no PDF span at all
(those get no overlap, so no emphasis).
"""

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import OcrItem
from app.services.document_processing import StyledSpan

#: A line is emphasized when at least this share of its overlap area with the
#: page's PDF spans is bold/italic. 0.6 tolerates a stray plain span (a trailing
#: period, a stray bullet) without demanding that every glyph on the line be
#: styled.
EMPHASIS_MIN_FRACTION = 0.6


def _intersection_area(first: Box, second: Box) -> float:
    horizontal = min(first.left + first.width, second.left + second.width) - max(
        first.left, second.left
    )
    vertical = min(first.top + first.height, second.top + second.height) - max(
        first.top, second.top
    )
    if horizontal <= 0 or vertical <= 0:
        return 0.0
    return horizontal * vertical


def annotate_emphasis(lines: list[OcrItem], styled_spans: list[StyledSpan]) -> list[OcrItem]:
    """Return `lines` with `emphasized` set on those matching bold/italic PDF text.

    Non-mutating: the OCR engine caches and shares its `OcrItem`s across features,
    so this returns copies (only for emphasized lines) rather than flipping a
    flag on the shared objects. With no styled spans, the input list is returned
    unchanged.
    """
    if not styled_spans:
        return lines

    annotated: list[OcrItem] = []
    for line in lines:
        total = 0.0
        emphasized = 0.0
        for span in styled_spans:
            area = _intersection_area(line.box, span.box)
            if area <= 0:
                continue
            total += area
            if span.bold or span.italic:
                emphasized += area
        if total > 0 and emphasized / total >= EMPHASIS_MIN_FRACTION:
            annotated.append(line.model_copy(update={"emphasized": True}))
        else:
            annotated.append(line)
    return annotated
