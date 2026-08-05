from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.enums import JobStage, JobStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    deck_name: str
    pdf_path: str
    page_count: int | None = Field(default=None)
    status: JobStatus = Field(default=JobStatus.PENDING)
    error_message: str | None = Field(default=None)
    # Progress columns are added by `_add_missing_columns` without NOT NULL, so
    # rows created before this feature read back NULL even for `stage_completed`.
    # Always read it as `(job.stage_completed or 0)`.
    stage: JobStage | None = Field(default=None)
    stage_completed: int = Field(default=0)
    stage_total: int | None = Field(default=None)
    processing_started_at: datetime | None = Field(default=None)
    # Resolved `GenerationOptions` serialized to JSON (see schemas/generation_options.py).
    # Nullable: `_add_missing_columns` cannot emit NOT NULL, so rows created before
    # this feature read back NULL and resolve to the documented defaults.
    options: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
