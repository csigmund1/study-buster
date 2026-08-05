"""Deterministic validation and cross-group near-duplicate removal (plan.md §10).

Invalid or duplicate cards are dropped silently; nothing here is fatal to the
pipeline. This runs after all `CardGenerator` calls, over the full flat list of
generated cards so duplicates are caught across page groups.
"""

import re
import string

from app.models.enums import NoteType
from app.services.card_generation.schemas import GeneratedCard
from app.services.card_rules import CardValidationError, validate_card_fields

MAX_FIELD_LENGTH = 2000

_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    lowered = text.lower().translate(_PUNCTUATION_TABLE)
    return _WHITESPACE_PATTERN.sub(" ", lowered).strip()


def _dedup_key(card: GeneratedCard) -> str:
    text = card.cloze_text if card.note_type == "cloze" else card.front
    return normalize_text(text or "")


def _is_within_length(card: GeneratedCard) -> bool:
    for field in (card.front, card.back, card.cloze_text):
        if field is not None and len(field) > MAX_FIELD_LENGTH:
            return False
    return True


def _is_valid(card: GeneratedCard, page_count: int) -> bool:
    if card.note_type not in (NoteType.BASIC.value, NoteType.CLOZE.value):
        return False
    if not (1 <= card.source_page <= page_count):
        return False
    if not _is_within_length(card):
        return False
    try:
        validate_card_fields(NoteType(card.note_type), card.front, card.back, card.cloze_text)
    except CardValidationError:
        return False
    return True


def validate_and_dedupe(cards: list[GeneratedCard], page_count: int) -> list[GeneratedCard]:
    """Drop invalid cards and cross-group near-duplicates, preserving order."""
    valid_cards = [card for card in cards if _is_valid(card, page_count)]

    seen_keys: set[str] = set()
    deduped: list[GeneratedCard] = []
    for card in valid_cards:
        key = _dedup_key(card)
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        deduped.append(card)

    return deduped
