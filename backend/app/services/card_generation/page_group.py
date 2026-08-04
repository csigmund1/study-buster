"""A contiguous group of PDF pages passed to a `CardGenerator` in one call."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GroupPage:
    """One page within a `PageGroup`."""

    page_number: int  # 1-indexed, matches the real PDF page number
    image_path: Path
    supplemental_text: str


@dataclass(frozen=True)
class PageGroup:
    deck_name: str
    pages: list[GroupPage]
