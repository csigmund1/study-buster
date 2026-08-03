from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class NoteType(StrEnum):
    BASIC = "basic"
    CLOZE = "cloze"
    DIAGRAM = "diagram"
