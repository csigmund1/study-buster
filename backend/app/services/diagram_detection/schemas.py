"""Structured-output schema for diagram detection (promoted from spikes/).

This is the raw shape a `DiagramDetector` returns for one page. The pipeline maps
a `DiagramDetection` into per-card `Occlusion` payloads (see models/occlusion.py).
All boxes are page-normalized `[0, 1]` (`Box`), reusing the domain type so
detection, storage, composition, and export share one coordinate space.
"""

from pydantic import BaseModel, Field

from app.models.occlusion import Box


class LabelDetection(BaseModel):
    text: str = Field(description="The label's literal text, e.g. 'Thyroid gland'.")
    label_box: Box = Field(description="Tight box around the label TEXT itself.")


class DiagramDetection(BaseModel):
    is_labeled_diagram: bool = Field(
        description="True only if the page contains a diagram whose parts are labeled "
        "with text connected by arrows or leader lines."
    )
    diagram_box: Box | None = Field(
        default=None,
        description="OPTIONAL rough hint around the whole figure region, used only as a "
        "crop seed. Never trusted as the crop itself — the actual crop is derived "
        "deterministically from label boxes plus whitespace-gutter snapping.",
    )
    labels: list[LabelDetection] = Field(default_factory=list)
