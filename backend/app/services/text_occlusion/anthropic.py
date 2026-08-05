"""Text-span selection: local Apple Vision OCR for geometry (done by the caller),
one `claude-haiku-4-5` call per page for the semantic "what is worth masking".

The model never predicts geometry. It is shown the page image plus the numbered
OCR lines and returns character ranges inside those lines; `spans.py` turns
those ranges into boxes from the OCR word geometry, and `filters.py` decides
what survives.
"""

import base64
import io

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam
from PIL import Image

from app.services.diagram_detection.ocr import OcrItem
from app.services.text_occlusion.base import TextOcclusionError, TextPage
from app.services.text_occlusion.filters import MAX_SPAN_WORDS, MAX_SPANS_PER_PAGE
from app.services.text_occlusion.schemas import SpanSelection

MAX_TOKENS = 2048

SYSTEM_PROMPT = f"""\
This is a lecture slide. Apple Vision OCR extracted numbered lines of text from \
the image; each line is given with its index and its exact text in the user \
message.

Choose the phrases a student should be quizzed on by hiding them on the slide \
image (fill-in-the-blank). For each chosen phrase return the character range \
inside the line it occupies: line_index, char_start (0-based offset into that \
line's text) and char_length.

Rules:
- Choose key terms or short noun phrases: 1-{MAX_SPAN_WORDS} words, never a \
whole line, never a bare function word (the, of, is, ...).
- Leave enough surrounding text visible that the blank is answerable.
- Never choose overlapping phrases, and never choose the same phrase twice.
- Skip lines whose text looks garbled or misrecognized.
- Return at most {MAX_SPANS_PER_PAGE} spans, best first. If nothing on the slide \
is worth masking, return an empty spans list.
- `answer` must be exactly the characters covered by the range.
- If a phrase wraps onto the next line, return one ref per line fragment in the \
same span.

Also return is_labeled_diagram: true only if this slide contains a diagram whose \
parts are named by text connected to the drawing via arrows or leader lines.

Only use line indices that appear in the numbered line list.
"""


def _build_line_list(lines: list[OcrItem]) -> str:
    return "\n".join(f'[{index}] "{line.text}"' for index, line in enumerate(lines))


class AnthropicTextSpanSelector:
    def __init__(self, model: str, max_edge_px: int) -> None:
        self._model = model
        self._max_edge_px = max_edge_px
        self._client = anthropic.Anthropic()

    def select(self, page: TextPage) -> SpanSelection:
        if not page.lines:
            return SpanSelection()

        image_block = self._encode_page(page)
        text_block: TextBlockParam = {
            "type": "text",
            "text": f"Page {page.page_number}. Numbered OCR lines:\n"
            f"{_build_line_list(page.lines)}",
        }

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": [image_block, text_block]}],
                output_format=SpanSelection,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise TextOcclusionError(f"Text span selection request failed: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise TextOcclusionError(
                "Text span selection response could not be parsed into the expected schema."
            )
        return parsed

    def _encode_page(self, page: TextPage) -> ImageBlockParam:
        with Image.open(page.image_path) as raw_image:
            image = raw_image.convert("RGB")
        long_edge = max(image.width, image.height)
        if long_edge > self._max_edge_px:
            scale = self._max_edge_px / long_edge
            new_size = (round(image.width * scale), round(image.height * scale))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
            },
        }
