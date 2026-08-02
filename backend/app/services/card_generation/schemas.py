"""Pydantic schemas for structured card-generation output (plan.md §8-§9)."""

from typing import Literal

from pydantic import BaseModel


class GeneratedCard(BaseModel):
    note_type: Literal["basic", "cloze"]
    front: str | None = None
    back: str | None = None
    cloze_text: str | None = None
    source_page: int
    needs_page_image: bool = False


class GeneratedCards(BaseModel):
    cards: list[GeneratedCard]
