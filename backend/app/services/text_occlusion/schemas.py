"""Wire and internal shapes for the text-occlusion quality pipeline.

Both model passes name phrases by exact text inside numbered OCR lines. Internal
refs may additionally carry deterministic OCR word offsets so repeated phrases
on one line remain independently addressable. No model predicts coordinates:
geometry is derived in `spans.py` by unioning the referenced OCR word boxes.

The preview is layout context only. Because the model must select from the exact
OCR text it was shown, every mask remains locatable without reconciling image
transcription against OCR.
"""

from pydantic import BaseModel, Field, model_validator


class ModelSpanRef(BaseModel):
    """One exact line-local phrase returned by a model call."""

    line_index: int = Field(
        ge=0, description="Index into that page's numbered OCR line list."
    )
    text: str = Field(
        description="The phrase to hide, spelled exactly as it reads on that line. "
        "If the phrase wraps onto another line, give only this line's fragment here."
    )


class SpanRef(ModelSpanRef):
    """A model phrase, optionally anchored to an exact deterministic word range."""

    word_start: int | None = Field(
        default=None,
        ge=0,
        description="Deterministic word offset within the OCR line. Model selectors omit this.",
    )
    word_count: int | None = Field(
        default=None,
        ge=1,
        description="Number of OCR words at word_start. Model selectors omit this.",
    )

    @model_validator(mode="after")
    def validate_word_range(self) -> "SpanRef":
        if (self.word_start is None) != (self.word_count is None):
            raise ValueError("word_start and word_count must be provided together")
        return self


class CandidateSpan(BaseModel):
    """One pedagogical target proposed by the visual selection pass."""

    page_number: int = Field(ge=1, description="1-indexed page the target is on.")
    refs: list[ModelSpanRef] = Field(
        description="The answer occurrence: one ref per line it occupies, in reading order."
    )


class CandidateBatch(BaseModel):
    """Every raw learning target proposed across one visual selection batch."""

    spans: list[CandidateSpan] = Field(default_factory=list)


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


class RefGroup(BaseModel):
    """One semantic alias or answer-revealing phrase, possibly line-wrapped."""

    refs: list[ModelSpanRef] = Field(
        min_length=1,
        description="One exact OCR ref per line occupied by this revealing phrase.",
    )


class AuditDecision(BaseModel):
    """The text-only auditor's final decision for one numbered candidate."""

    candidate_index: int = Field(ge=0)
    keep: bool
    additional_leakage: list[RefGroup] = Field(default_factory=list)


class BatchAudit(BaseModel):
    """One decision for every candidate submitted to the text-only audit."""

    decisions: list[AuditDecision] = Field(default_factory=list)
