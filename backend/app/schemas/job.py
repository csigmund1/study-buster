from datetime import datetime

from pydantic import BaseModel

from app.models.enums import JobStatus


class JobRead(BaseModel):
    id: int
    deck_name: str
    status: JobStatus
    error_message: str | None
    page_count: int | None
    card_count: int
    created_at: datetime
    updated_at: datetime
