"""Wire shapes for text-occlusion span selection.

A selector names spans by *character range inside a numbered OCR line*, never by
coordinates: geometry is derived deterministically in `spans.py` from the OCR
word boxes those ranges cover.
"""

from pydantic import BaseModel, Field


class SpanRef(BaseModel):
    """One line-local character range within the OCR line list handed to the selector."""

    line_index: int = Field(description="Index into the numbered OCR line list.")
    char_start: int = Field(description="Start character offset within that line's text.")
    char_length: int = Field(description="Length in characters of the masked range.")


class SelectedSpan(BaseModel):
    """One phrase to mask. More than one ref only when the phrase wraps lines."""

    refs: list[SpanRef] = Field(
        description="One ref per line the phrase occupies, in reading order."
    )
    answer: str = Field(description="The masked phrase's text, as it reads on the slide.")


class SpanSelection(BaseModel):
    """One page's selection result."""

    is_labeled_diagram: bool = Field(
        default=False,
        description="True only if this slide contains a diagram whose parts are named "
        "by text connected to the drawing via arrows or leader lines.",
    )
    spans: list[SelectedSpan] = Field(default_factory=list)
