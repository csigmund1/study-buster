"""Selects a `DiagramDetector` implementation from settings (plan.md §5, §14)."""

from app.config import Settings
from app.services.diagram_detection.base import DiagramDetector


def get_diagram_detector(settings: Settings) -> DiagramDetector:
    if settings.diagram_detector == "anthropic":
        from app.services.diagram_detection.anthropic import AnthropicDiagramDetector
        from app.services.diagram_detection.ocr import AppleVisionOcr

        return AnthropicDiagramDetector(
            settings.diagram_detection_model,
            settings.detection_max_edge_px,
            AppleVisionOcr(),
        )
    if settings.diagram_detector == "mock":
        from app.services.diagram_detection.mock import MockDiagramDetector

        return MockDiagramDetector()
    raise ValueError(f"Unknown DIAGRAM_DETECTOR: {settings.diagram_detector!r}")
