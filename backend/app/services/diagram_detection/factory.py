"""Selects a `DiagramDetector` implementation from settings (plan.md §5, §14)."""

from app.config import Settings
from app.services.diagram_detection.base import DiagramDetector
from app.services.diagram_detection.ocr import OcrEngine


def get_diagram_detector(settings: Settings, ocr: OcrEngine | None = None) -> DiagramDetector:
    """Build the configured detector.

    `ocr` lets the pipeline share one cached engine across features so a page is
    OCR'd once per job. Omitting it keeps the previous standalone behavior.
    """
    if settings.diagram_detector == "anthropic":
        from app.services.diagram_detection.anthropic import AnthropicDiagramDetector
        from app.services.diagram_detection.ocr import AppleVisionOcr

        return AnthropicDiagramDetector(
            settings.diagram_detection_model,
            settings.detection_max_edge_px,
            ocr if ocr is not None else AppleVisionOcr(),
        )
    if settings.diagram_detector == "mock":
        from app.services.diagram_detection.mock import MockDiagramDetector

        return MockDiagramDetector()
    raise ValueError(f"Unknown DIAGRAM_DETECTOR: {settings.diagram_detector!r}")
