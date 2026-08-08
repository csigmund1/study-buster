"""Wire shapes for text-occlusion span selection.

The selector is shown a low-resolution slide preview plus the OCR text itself and
names spans by *phrase text inside a numbered OCR line*. A selected span owns its
page number once; its refs therefore only need `(line_index, text)`. It never
returns coordinates: geometry is derived deterministically in `spans.py` by
locating each phrase among the line's OCR words and unioning their boxes.

The preview is layout context only. Because the model must select from the exact
OCR text it was shown, every mask remains locatable without reconciling image
transcription against OCR.
"""

from pydantic import BaseModel, Field


class SpanRef(BaseModel):
    """One line-local phrase within the numbered OCR lines handed to the selector."""

    line_index: int = Field(
        ge=0, description="Index into that page's numbered OCR line list."
    )
    text: str = Field(
        description="The phrase to hide, spelled exactly as it reads on that line. "
        "If the phrase wraps onto another line, give only this line's fragment here."
    )


class SelectedSpan(BaseModel):
    """One learning target and every place it must be hidden on one slide.

    `refs` identifies the best-context occurrence and is the sole source of the
    card's answer. `leakage_refs` identifies repeated occurrences or obvious
    equivalents that would reveal that answer; those boxes are hidden too but
    never appended to the answer text.
    """

    page_number: int = Field(ge=1, description="1-indexed page the learning target is on.")
    refs: list[SpanRef] = Field(
        description="The answer occurrence: one ref per line it occupies, in reading order."
    )
    leakage_refs: list[SpanRef] = Field(
        default_factory=list,
        description="Other occurrences on this page that would reveal the answer if visible.",
    )


class BatchSelection(BaseModel):
    """One batch's selection result: every mask chosen across the batch's pages."""

    spans: list[SelectedSpan] = Field(default_factory=list)
