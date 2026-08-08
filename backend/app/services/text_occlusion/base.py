"""`TextSpanSelector` protocol: picks which OCR text spans to mask across a deck.

Selection is text-only and batched: the selector is handed several already-OCR'd
pages at once and returns a flat list of spans, each addressed by
`(page_number, line_index)` via its refs. No page image is sent — the model
chooses from the OCR text it is shown, so every returned phrase is locatable.
"""

from dataclasses import dataclass
from typing import Protocol

from app.services.diagram_detection.ocr import OcrItem
from app.services.text_occlusion.schemas import SelectedSpan


class TextOcclusionError(RuntimeError):
    """Raised when a `TextSpanSelector` cannot produce a selection for a batch."""


@dataclass(frozen=True)
class TextPage:
    """One rendered page, already OCR'd, passed to a `TextSpanSelector`.

    `lines` is the numbered list a selector refers to by index; the pipeline OCRs
    each page once with a shared engine and hands the result in here. The selector
    keeps every line's original index (so `spans.py` can address `lines`
    downstream) while omitting furniture from the listing the model sees.
    """

    page_number: int  # 1-indexed, matches the real PDF page number
    lines: list[OcrItem]


class TextSpanSelector(Protocol):
    def select(
        self,
        pages: list[TextPage],
        chrome_lines: frozenset[str] = frozenset(),
        objectives: str | None = None,
    ) -> list[SelectedSpan]:
        """Return the spans worth masking across `pages`, addressed by page + line.

        `chrome_lines` is the deck's furniture (repeated headers/footers, page
        numbers); the selector omits those lines from what the model sees.
        `objectives` is the deck's learning-objectives text, when it has one:
        context that steers which spans are worth choosing, never a mask source.
        """
        ...
