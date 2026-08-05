"""Domain shapes for diagram (image-occlusion) cards.

These are plain Pydantic models (not tables). A `CardDraft` of note type
`diagram` stores its `Occlusion` as JSON; the API serializes it back to these
types. All coordinates are **page-normalized** floats in `[0, 1]` over the full
rendered `source_page` image, origin top-left. Composition and export convert to
crop-local pixels.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Direction(StrEnum):
    IDENTIFY = "identify"  # target highlighted, its label hidden; answer reveals the label
    LOCATE = "locate"  # target named in text, all labels masked; answer highlights it


class OcclusionKind(StrEnum):
    """Which detector produced an occlusion.

    Note the deliberate asymmetry with `NoteType`: note type `diagram` pairs with
    kind `diagram`, but note type `text_occlusion` pairs with kind `text`.
    """

    DIAGRAM = "diagram"
    TEXT = "text"


class Box(BaseModel):
    """Axis-aligned rectangle, page-normalized to [0, 1] (origin top-left)."""

    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class Occlusion(BaseModel):
    """The occlusion payload carried by an occlusion card (`diagram` or
    `text_occlusion`).

    `mask_boxes` is everything hidden on the question side — for a diagram, every
    label's text box on the page (including the target's); for a text card, the
    masked span's boxes. `target_boxes` is what the answer side reveals and what
    export clozes, all under a single `c1` index so one note is one Anki card.

    `target_boxes` is **not** parallel to `labels`: one label may span several
    boxes when a masked phrase wraps across lines.
    """

    kind: OcclusionKind
    direction: Direction
    labels: list[str] = Field(min_length=1)
    crop_box: Box
    target_boxes: list[Box] = Field(min_length=1)
    mask_boxes: list[Box]

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data: Any) -> Any:
        """Upgrade pre-multi-target payloads read back from the database.

        Rows persisted before this change carry `label`/`label_box` and no
        `kind`. They were all diagram cards. Upgrading here (rather than
        migrating) keeps existing jobs loadable with no dev-DB reset — see
        `storage/database.py`, which cannot rename columns.
        """
        if not isinstance(data, dict) or "kind" in data:
            return data
        if "label" not in data and "label_box" not in data:
            return data

        upgraded = dict(data)
        legacy_label = upgraded.pop("label", None)
        legacy_box = upgraded.pop("label_box", None)
        upgraded["kind"] = OcclusionKind.DIAGRAM
        if "labels" not in upgraded and legacy_label is not None:
            upgraded["labels"] = [legacy_label]
        if "target_boxes" not in upgraded and legacy_box is not None:
            upgraded["target_boxes"] = [legacy_box]
        return upgraded
