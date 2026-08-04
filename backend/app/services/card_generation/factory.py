"""Selects a `CardGenerator` implementation from settings (plan.md §5, §14)."""

from app.config import Settings
from app.services.card_generation.base import CardGenerator


def get_card_generator(settings: Settings) -> CardGenerator:
    if settings.card_generator == "anthropic":
        from app.services.card_generation.anthropic_generator import AnthropicCardGenerator

        return AnthropicCardGenerator()
    if settings.card_generator == "mock":
        from app.services.card_generation.mock_generator import MockCardGenerator

        return MockCardGenerator()
    raise ValueError(f"Unknown CARD_GENERATOR: {settings.card_generator!r}")
