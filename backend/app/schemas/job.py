from datetime import datetime

from pydantic import BaseModel

from app.models.enums import JobStage, JobStatus
from app.schemas.generation_options import GenerationOptions


class JobRead(BaseModel):
    id: int
    deck_name: str
    status: JobStatus
    error_message: str | None
    page_count: int | None
    card_count: int
    stage: JobStage | None
    stage_label: str | None
    progress_percent: float | None
    eta_seconds: int | None
    #: The resolved generation options that produced this job; always populated.
    options: GenerationOptions
    created_at: datetime
    updated_at: datetime
