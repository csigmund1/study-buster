"""Diagram-label detection: local Apple Vision OCR for geometry, a double-pass
claude-haiku-4-5 classifier for semantics (adapted from spikes/haiku_label_filter.py).

The classifier never predicts box geometry — it only selects which
already-OCR'd text items are diagram labels (and groups items that together
form one label phrase). Boxes are derived from the OCR items themselves.
"""

import base64
import io
import logging

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam
from PIL import Image
from pydantic import BaseModel, Field

from app.models.occlusion import Box
from app.services.diagram_detection.base import DetectionPage, DiagramDetectionError
from app.services.diagram_detection.cropping import union_box
from app.services.diagram_detection.ocr import OcrEngine, OcrItem
from app.services.diagram_detection.schemas import DiagramDetection, LabelDetection

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
RUNS_PER_PAGE = 2

SYSTEM_PROMPT = """\
This is a lecture slide. Apple Vision OCR extracted numbered text items from the \
image; each item's normalized bounding box (left/top/width/height, origin \
top-left) is given alongside its text in the user message.

Identify which of these numbered items are labels of a diagram: text that names a \
part of a drawing/figure, typically connected to it via an arrow or leader line. \
Exclude slide titles, bullet/body text (paragraphs, sentences), page numbers, \
citations, watermarks/logos, figure captions (e.g. "Anterior view"), and legends \
or color keys.

If a single label's text was split across multiple adjacent OCR items (e.g. a name \
and a parenthetical split into two boxes), group their indices together into one \
LabelGroup with the combined label_text.

Also return diagram_box: an OPTIONAL rough box around the whole figure region, only \
as a hint -- it is not used as the final crop.

Only return indices that appear in the numbered item list. If the slide has no \
diagram labels, return an empty labels list and is_labeled_diagram=false.
"""


class LabelGroup(BaseModel):
    item_indices: list[int] = Field(
        description="Indices (from the numbered item list) of one or more OCR "
        "items that together form one label phrase, e.g. if Vision split "
        "'Subcostal' and '(T12)' into two items, list both indices here."
    )
    label_text: str = Field(description="The combined label text, e.g. 'Subcostal (T12)'.")


class ClassifierResult(BaseModel):
    is_labeled_diagram: bool = Field(
        description="True only if this slide contains a diagram whose parts are "
        "named by text connected to the drawing via arrows or leader lines."
    )
    diagram_box: Box | None = Field(
        default=None, description="Optional rough hint box around the whole figure."
    )
    labels: list[LabelGroup] = Field(default_factory=list)


def _build_item_list(items: list[OcrItem]) -> str:
    lines = []
    for i, item in enumerate(items):
        box = item.box
        lines.append(
            f'[{i}] "{item.text}" @ '
            f"({round(box.left, 3)}, {round(box.top, 3)}, "
            f"{round(box.width, 3)}, {round(box.height, 3)})"
        )
    return "\n".join(lines)


def _union_results(
    passes: list[ClassifierResult], num_items: int
) -> tuple[bool, Box | None, list[tuple[frozenset[int], str]]]:
    """Union two classifier passes: dedupe identical index sets (first label_text
    wins), then drop any group whose index set is a strict subset of another kept
    group. Indices outside `range(num_items)` are discarded."""
    kept: dict[frozenset[int], str] = {}
    for result in passes:
        for group in result.labels:
            indices = frozenset(i for i in group.item_indices if 0 <= i < num_items)
            if not indices:
                continue
            if indices not in kept:
                kept[indices] = group.label_text

    sets = list(kept.keys())
    final: dict[frozenset[int], str] = {}
    for indices in sets:
        if any(indices < other and indices != other for other in sets):
            continue
        final[indices] = kept[indices]

    is_labeled_diagram = any(result.is_labeled_diagram for result in passes)
    diagram_box = next((result.diagram_box for result in passes if result.diagram_box), None)
    return is_labeled_diagram, diagram_box, list(final.items())


class AnthropicDiagramDetector:
    def __init__(self, model: str, max_edge_px: int, ocr: OcrEngine) -> None:
        self._model = model
        self._max_edge_px = max_edge_px
        self._ocr = ocr
        self._client = anthropic.Anthropic()

    def detect(self, page: DetectionPage) -> DiagramDetection:
        items = self._ocr.extract(page.image_path)
        if not items:
            return DiagramDetection(is_labeled_diagram=False)

        image_block = self._encode_page(page)
        item_list = _build_item_list(items)
        text_block: TextBlockParam = {
            "type": "text",
            "text": f"Page {page.page_number}. Numbered OCR items:\n{item_list}",
        }

        passes: list[ClassifierResult] = []
        for _ in range(RUNS_PER_PAGE):
            passes.append(self._classify(image_block, text_block))

        is_labeled_diagram, diagram_box, groups = _union_results(passes, len(items))

        labels: list[LabelDetection] = []
        for indices, label_text in groups:
            boxes = [items[i].box for i in indices]
            labels.append(LabelDetection(text=label_text, label_box=union_box(boxes)))

        if is_labeled_diagram and len(labels) <= max(1, len(items) // 10):
            logger.warning(
                "Low label yield on page %d: %d OCR items, %d labels selected",
                page.page_number,
                len(items),
                len(labels),
            )

        return DiagramDetection(
            is_labeled_diagram=is_labeled_diagram, diagram_box=diagram_box, labels=labels
        )

    def _classify(
        self, image_block: ImageBlockParam, text_block: TextBlockParam
    ) -> ClassifierResult:
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
                output_format=ClassifierResult,
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise DiagramDetectionError(f"Diagram detection request failed: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise DiagramDetectionError(
                "Diagram detection response could not be parsed into the expected schema."
            )
        return parsed

    def _encode_page(self, page: DetectionPage) -> ImageBlockParam:
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
