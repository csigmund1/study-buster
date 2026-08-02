from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from app.models.enums import NoteType


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CardDraft(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    note_type: NoteType
    front: str | None = Field(default=None)
    back: str | None = Field(default=None)
    cloze_text: str | None = Field(default=None)
    source_page: int | None = Field(default=None)
    needs_page_image: bool = Field(default=False)
    is_deleted: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
