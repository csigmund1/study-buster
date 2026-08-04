"""`CardGenerator` protocol (plan.md §5): content understanding behind one interface."""

from typing import Protocol

from app.services.card_generation.page_group import PageGroup
from app.services.card_generation.schemas import GeneratedCards


class CardGenerationError(RuntimeError):
    """Raised when a `CardGenerator` cannot produce cards for a group."""


class CardGenerator(Protocol):
    def generate(self, group: PageGroup) -> GeneratedCards:
        """Return the group's cards plus which of its pages hold labeled diagrams."""
        ...
