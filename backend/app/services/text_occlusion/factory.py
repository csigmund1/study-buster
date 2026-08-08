"""Selects a `TextSpanSelector` implementation from settings."""

from app.config import Settings
from app.services.text_occlusion.base import TextSpanSelector


def get_text_span_selector(settings: Settings) -> TextSpanSelector:
    """Build the configured selector.

    Takes no OCR engine: selectors receive their lines on `TextPage.lines`, which
    the pipeline fills from the one shared, cached engine it OCRs each page with.
    """
    if settings.text_occlusion_selector == "anthropic":
        from app.services.text_occlusion.anthropic import AnthropicTextSpanSelector

        return AnthropicTextSpanSelector(
            settings.text_occlusion_model, settings.text_occlusion_batch_pages
        )
    if settings.text_occlusion_selector == "mock":
        from app.services.text_occlusion.mock import MockTextSpanSelector

        return MockTextSpanSelector()
    raise ValueError(f"Unknown TEXT_OCCLUSION: {settings.text_occlusion_selector!r}")
