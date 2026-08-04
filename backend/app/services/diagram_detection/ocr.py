"""Local OCR (Apple Vision via `ocrmac`), adapted from spikes/ocr_boxes.py.

Provides page-normalized, top-left-origin text-item geometry; the semantic
"which items are diagram labels" decision is made separately (see
`anthropic.py`), never here.
"""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.models.occlusion import Box
from app.services.diagram_detection.base import DiagramDetectionError


class OcrItem(BaseModel):
    text: str
    box: Box


class OcrEngine(Protocol):
    def extract(self, image_path: Path) -> list[OcrItem]:
        """Return every recognized text item on the page, page-normalized."""
        ...


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class AppleVisionOcr:
    """OCR via the macOS Vision framework (`ocrmac`)."""

    def extract(self, image_path: Path) -> list[OcrItem]:
        try:
            from ocrmac import ocrmac
        except Exception as exc:  # pragma: no cover - import failure path
            raise DiagramDetectionError(f"ocrmac is unavailable: {exc}") from exc

        try:
            results = ocrmac.OCR(str(image_path), recognition_level="accurate").recognize()
        except Exception as exc:
            raise DiagramDetectionError(f"Apple Vision OCR failed: {exc}") from exc

        items: list[OcrItem] = []
        for text, _conf, bbox in results:
            # ocrmac bbox is normalized [0, 1], origin bottom-left: (x, y, width, height).
            x, y, bw, bh = bbox
            left = _clamp01(x)
            right = _clamp01(x + bw)
            top = _clamp01(1 - y - bh)
            bottom = _clamp01(1 - y)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                continue
            box = Box(left=left, top=top, width=width, height=height)
            items.append(OcrItem(text=text, box=box))
        return items
