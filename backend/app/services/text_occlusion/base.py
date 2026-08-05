"""`TextSpanSelector` protocol: picks which OCR text spans on one page to mask."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.diagram_detection.ocr import OcrItem
from app.services.text_occlusion.schemas import SpanSelection


class TextOcclusionError(RuntimeError):
    """Raised when a `TextSpanSelector` cannot produce a selection for a page."""


@dataclass(frozen=True)
class TextPage:
    """One rendered page, already OCR'd, passed to a `TextSpanSelector`.

    `lines` is the numbered list a selector refers to by index; the pipeline
    OCRs each page once with a shared engine and hands the result in here.
    """

    page_number: int  # 1-indexed, matches the real PDF page number
    image_path: Path
    lines: list[OcrItem]


class TextSpanSelector(Protocol):
    def select(self, page: TextPage) -> SpanSelection:
        """Return the spans worth masking on this page."""
        ...
