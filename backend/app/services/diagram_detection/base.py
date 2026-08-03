"""`DiagramDetector` protocol: locates labeled diagrams within one rendered page."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.diagram_detection.schemas import DiagramDetection


class DiagramDetectionError(RuntimeError):
    """Raised when a `DiagramDetector` cannot produce a detection for a page."""


@dataclass(frozen=True)
class DetectionPage:
    """One rendered page passed to a `DiagramDetector`."""

    page_number: int  # 1-indexed, matches the real PDF page number
    image_path: Path


class DiagramDetector(Protocol):
    def detect(self, page: DetectionPage) -> DiagramDetection:
        """Return the page's diagram/label detections."""
        ...
