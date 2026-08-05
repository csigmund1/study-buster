from app.services.text_occlusion.base import TextOcclusionError, TextPage, TextSpanSelector
from app.services.text_occlusion.factory import get_text_span_selector
from app.services.text_occlusion.filters import AcceptedSpan, accept_spans
from app.services.text_occlusion.mock import MockTextSpanSelector
from app.services.text_occlusion.schemas import SelectedSpan, SpanRef, SpanSelection

__all__ = [
    "AcceptedSpan",
    "MockTextSpanSelector",
    "SelectedSpan",
    "SpanRef",
    "SpanSelection",
    "TextOcclusionError",
    "TextPage",
    "TextSpanSelector",
    "accept_spans",
    "get_text_span_selector",
]
