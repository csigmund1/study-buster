"""Deterministic validation rules for card drafts (contract §CardDraft, plan.md §10)."""

import re

from app.models.enums import NoteType, is_occlusion

_CLOZE_PATTERN = re.compile(r"\{\{c\d+::.+?\}\}")


class CardValidationError(ValueError):
    """Raised when a card draft violates note-type rules."""


def is_valid_cloze_text(text: str) -> bool:
    return bool(text.strip()) and bool(_CLOZE_PATTERN.search(text))


def validate_card_fields(
    note_type: NoteType,
    front: str | None,
    back: str | None,
    cloze_text: str | None,
) -> None:
    """Raise `CardValidationError` if the field combination violates note-type rules."""
    if note_type == NoteType.BASIC:
        if not front or not front.strip():
            raise CardValidationError("Basic cards require a non-empty 'front'.")
        if not back or not back.strip():
            raise CardValidationError("Basic cards require a non-empty 'back'.")
    elif note_type == NoteType.CLOZE:
        if not cloze_text or not is_valid_cloze_text(cloze_text):
            raise CardValidationError(
                "Cloze cards require non-empty 'cloze_text' with valid {{c1::...}} syntax."
            )
    elif is_occlusion(note_type):
        # Occlusion cards carry question/answer text in front/back; the occlusion
        # geometry is validated separately (services/diagram_detection,
        # services/text_occlusion).
        if not front or not front.strip():
            raise CardValidationError("Occlusion cards require a non-empty 'front'.")
        if not back or not back.strip():
            raise CardValidationError("Occlusion cards require a non-empty 'back'.")
