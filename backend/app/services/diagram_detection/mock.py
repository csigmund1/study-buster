"""Deterministic fixture detector — zero-cost, no I/O beyond the page it is given."""

from app.models.occlusion import Box
from app.services.diagram_detection.base import DetectionPage
from app.services.diagram_detection.schemas import DiagramDetection, LabelDetection


class MockDiagramDetector:
    """Returns a fixed, geometry-valid `DiagramDetection` regardless of page content.

    The diagram box covers most of the page (used only as a rough crop hint);
    two labels sit inside it, each with its own label box.
    """

    def detect(self, page: DetectionPage) -> DiagramDetection:
        diagram_box = Box(left=0.15, top=0.05, width=0.6, height=0.9)
        labels = [
            LabelDetection(
                text="[mock] Structure A",
                label_box=Box(left=0.2, top=0.1, width=0.15, height=0.05),
            ),
            LabelDetection(
                text="[mock] Structure B",
                label_box=Box(left=0.2, top=0.75, width=0.15, height=0.05),
            ),
        ]
        return DiagramDetection(is_labeled_diagram=True, diagram_box=diagram_box, labels=labels)
