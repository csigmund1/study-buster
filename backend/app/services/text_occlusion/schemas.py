"""Wire shapes for text-occlusion span selection.

The selector is shown the OCR text itself and names spans by *phrase text inside
a numbered OCR line*, addressed by `(page_number, line_index)`. It never returns
coordinates: geometry is derived deterministically in `spans.py` by locating that
phrase among the line's OCR words and unioning their boxes.

Because the model selects from the exact OCR text it was shown, the phrase it
returns is present in that line by construction — so there is no char-offset
fallback and no per-page image to reconcile against.
"""

from pydantic import BaseModel, Field


class SpanRef(BaseModel):
    """One line-local phrase within the numbered OCR lines handed to the selector."""

    page_number: int = Field(description="1-indexed page the phrase is on.")
    line_index: int = Field(description="Index into that page's numbered OCR line list.")
    text: str = Field(
        description="The phrase to hide, spelled exactly as it reads on that line. "
        "If the phrase wraps onto another line, give only this line's fragment here."
    )


class SelectedSpan(BaseModel):
    """One phrase to mask. More than one ref only when the phrase wraps lines.

    Every ref of a span names the same page; a span never spans two pages.
    """

    refs: list[SpanRef] = Field(
        description="One ref per line the phrase occupies, in reading order."
    )
    answer: str = Field(description="The masked phrase's text, as it reads on the slide.")


class BatchSelection(BaseModel):
    """One batch's selection result: every mask chosen across the batch's pages."""

    spans: list[SelectedSpan] = Field(default_factory=list)
