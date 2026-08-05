from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobStage(StrEnum):
    """Pipeline stages, in the order the pipeline runs them.

    Declaration order is load-bearing: `job_progress.start_boundary` sums the
    weights of every stage declared before a given one.
    """

    RENDERING = "rendering"
    EXTRACTING = "extracting"
    GENERATING_CARDS = "generating_cards"
    DETECTING_MASKS = "detecting_masks"
    COMPOSING = "composing"
    FINALIZING = "finalizing"


class NoteType(StrEnum):
    BASIC = "basic"
    CLOZE = "cloze"
    DIAGRAM = "diagram"
    TEXT_OCCLUSION = "text_occlusion"


#: Note types backed by an `Occlusion` payload and composed question/answer
#: images. They are fixed-type (no note-type switching), their geometry is
#: read-only, and only `front`/`back` are editable.
OCCLUSION_NOTE_TYPES = frozenset({NoteType.DIAGRAM, NoteType.TEXT_OCCLUSION})


def is_occlusion(note_type: NoteType) -> bool:
    """True for note types carrying an `Occlusion` payload."""
    return note_type in OCCLUSION_NOTE_TYPES
