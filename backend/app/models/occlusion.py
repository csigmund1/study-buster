"""Domain shapes for diagram (image-occlusion) cards.

These are plain Pydantic models (not tables). A `CardDraft` of note type
`diagram` stores its `Occlusion` as JSON; the API serializes it back to these
types. All coordinates are **page-normalized** floats in `[0, 1]` over the full
rendered `source_page` image, origin top-left. Composition and export convert to
crop-local pixels.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Direction(StrEnum):
    IDENTIFY = "identify"  # target highlighted, its label hidden; answer reveals the label
    LOCATE = "locate"  # target named in text, all labels masked; answer highlights it


class Box(BaseModel):
    """Axis-aligned rectangle, page-normalized to [0, 1] (origin top-left)."""

    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class Occlusion(BaseModel):
    """The occlusion payload carried by a `diagram` card.

    `mask_boxes` is every label's text box (including the target's). `label_box`
    is the target label's own box: its mask is drawn in the highlight style on
    the question side (the pointer — "what is this?"), lifted on the answer
    side to reveal the label text, and covered by the single native-IO mask at
    export. There is no structure-level box: the label mask itself, sitting at
    the end of its arrow/leader line, is the pointer.
    """

    direction: Direction
    label: str
    crop_box: Box
    label_box: Box
    mask_boxes: list[Box]
