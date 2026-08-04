from datetime import datetime

from pydantic import BaseModel

from app.models.enums import NoteType
from app.models.occlusion import Occlusion


class CardDraftRead(BaseModel):
    id: int
    job_id: int
    note_type: NoteType
    front: str | None
    back: str | None
    cloze_text: str | None
    occlusion: Occlusion | None
    source_page: int | None
    needs_page_image: bool
    created_at: datetime
    updated_at: datetime


class CardDraftUpdate(BaseModel):
    """Partial update; unset fields are left unchanged."""

    front: str | None = None
    back: str | None = None
    cloze_text: str | None = None
    note_type: NoteType | None = None
    needs_page_image: bool | None = None
