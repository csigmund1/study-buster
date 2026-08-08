from app.services.text_occlusion.base import TextOcclusionError, TextPage, TextSpanSelector
from app.services.text_occlusion.document_context import (
    detect_chrome_lines,
    detect_objectives,
    is_chrome,
)
from app.services.text_occlusion.factory import get_text_span_selector
from app.services.text_occlusion.filters import AcceptedSpan, accept_spans
from app.services.text_occlusion.mock import MockTextSpanSelector
from app.services.text_occlusion.schemas import BatchSelection, SelectedSpan, SpanRef

__all__ = [
    "AcceptedSpan",
    "BatchSelection",
    "MockTextSpanSelector",
    "SelectedSpan",
    "SpanRef",
    "TextOcclusionError",
    "TextPage",
    "TextSpanSelector",
    "accept_spans",
    "detect_chrome_lines",
    "detect_objectives",
    "get_text_span_selector",
    "is_chrome",
]
