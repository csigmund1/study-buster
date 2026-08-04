from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from app.models.enums import JobStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    deck_name: str
    pdf_path: str
    page_count: int | None = Field(default=None)
    status: JobStatus = Field(default=JobStatus.PENDING)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
