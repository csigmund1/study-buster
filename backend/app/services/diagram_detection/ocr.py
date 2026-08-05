"""Local OCR via the macOS Vision framework, called directly through pyobjc.

Provides page-normalized, top-left-origin text-item geometry; the semantic
"which items are diagram labels" decision is made separately (see
`anthropic.py`), never here.

Two consumers share this module: diagram detection needs one box per recognized
line, and text occlusion needs a box for an arbitrary *character range* inside a
line. Vision gives both from a single recognition pass, so `extract()` computes
per-word boxes eagerly (measured at ~1-2 ms/page) and no Objective-C object ever
escapes this module.

`AppleVisionOcr` caches by resolved image path so a page rendered once is never
OCR'd twice, no matter how many features ask for it.
"""

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.models.occlusion import Box
from app.services.diagram_detection.base import DiagramDetectionError

#: Vision recognition levels: 0 = accurate, 1 = fast.
_RECOGNITION_LEVEL_ACCURATE = 0


class OcrWord(BaseModel):
    """One whitespace-delimited word within a recognized line.

    `char_start`/`char_length` index into the parent `OcrItem.text`, so a caller
    can name a span by character range and recover its geometry by unioning the
    words it covers.
    """

    text: str
    char_start: int
    char_length: int
    box: Box


class OcrItem(BaseModel):
    """One recognized line of text, with page-normalized geometry."""

    text: str
    box: Box
    #: Vision's confidence for this line, in [0, 1]. Clean printed headings score
    #: ~1.0; handwritten annotations measured 0.30-0.50. The quality lever for
    #: text occlusion is a floor on this value.
    confidence: float = 1.0
    #: Per-word boxes, in text order. Empty for engines that cannot supply them.
    words: list[OcrWord] = Field(default_factory=list)


class OcrEngine(Protocol):
    def extract(self, image_path: Path) -> list[OcrItem]:
        """Return every recognized text item on the page, page-normalized."""
        ...


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _box_from_bottom_left(x: float, y: float, width: float, height: float) -> Box | None:
    """Convert Vision's bottom-left `(x, y, w, h)` to our top-left `Box`.

    Returns `None` for degenerate geometry — `Box` would raise, and a bad span
    must be dropped rather than fail the page.
    """
    left = _clamp01(x)
    right = _clamp01(x + width)
    top = _clamp01(1 - y - height)
    bottom = _clamp01(1 - y)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return Box(left=left, top=top, width=right - left, height=bottom - top)


def _box_from_quad(rect_observation: Any) -> Box | None:
    """Convert a `VNRectangleObservation` (four corners, bottom-left origin).

    `boundingBoxForRange_error_` returns corners rather than a rect, so the flip
    is `top = 1 - max(y)`. Degenerate quads yield `None`.
    """
    corners = [
        rect_observation.bottomLeft(),
        rect_observation.bottomRight(),
        rect_observation.topLeft(),
        rect_observation.topRight(),
    ]
    xs = [corner.x for corner in corners]
    ys = [corner.y for corner in corners]
    left = _clamp01(min(xs))
    right = _clamp01(max(xs))
    top = _clamp01(1 - max(ys))
    bottom = _clamp01(1 - min(ys))
    if right - left <= 0 or bottom - top <= 0:
        return None
    return Box(left=left, top=top, width=right - left, height=bottom - top)


def _word_spans(text: str) -> list[tuple[int, int]]:
    """`(char_start, char_length)` for each whitespace-delimited word in `text`."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                spans.append((start, index - start))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(text) - start))
    return spans


def union_boxes(boxes: list[Box]) -> Box | None:
    """Tight box covering every input box, or `None` if there are none.

    Vision's range box for a phrase equals the union of its word boxes, so this
    is how a multi-word span recovers its geometry.
    """
    if not boxes:
        return None
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.left + box.width for box in boxes)
    bottom = max(box.top + box.height for box in boxes)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return Box(left=left, top=top, width=right - left, height=bottom - top)


class AppleVisionOcr:
    """OCR via the macOS Vision framework (pyobjc, no `ocrmac` indirection).

    Results are cached per resolved image path for the lifetime of the instance:
    the pipeline builds one engine per job and shares it across features.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, list[OcrItem]] = {}

    def extract(self, image_path: Path) -> list[OcrItem]:
        key = image_path.resolve()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        items = self._recognize(image_path)
        self._cache[key] = items
        return items

    def _recognize(self, image_path: Path) -> list[OcrItem]:
        try:
            import objc
            import Vision
        except Exception as exc:  # pragma: no cover - import failure path
            raise DiagramDetectionError(f"Apple Vision is unavailable: {exc}") from exc

        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise DiagramDetectionError(f"Could not read page image: {exc}") from exc

        items: list[OcrItem] = []
        try:
            with objc.autorelease_pool():
                request = Vision.VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(_RECOGNITION_LEVEL_ACCURATE)
                handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
                    data, None
                )
                handler.performRequests_error_([request], None)

                for observation in request.results() or []:
                    item = _build_item(observation)
                    if item is not None:
                        items.append(item)
        except DiagramDetectionError:
            raise
        except Exception as exc:
            raise DiagramDetectionError(f"Apple Vision OCR failed: {exc}") from exc
        return items


def _build_item(observation: Any) -> OcrItem | None:
    """Map one `VNRecognizedTextObservation` into an `OcrItem`, or `None`.

    The line box comes from the observation's own `boundingBox` — the same value
    `ocrmac` surfaced — so diagram detection sees byte-identical geometry.
    """
    candidates = observation.topCandidates_(1)
    if not candidates:
        return None
    candidate = candidates[0]
    text = candidate.string()
    if not text:
        return None

    bounding_box = observation.boundingBox()
    line_box = _box_from_bottom_left(
        bounding_box.origin.x,
        bounding_box.origin.y,
        bounding_box.size.width,
        bounding_box.size.height,
    )
    if line_box is None:
        return None

    return OcrItem(
        text=text,
        box=line_box,
        confidence=float(candidate.confidence()),
        words=_build_words(candidate, text),
    )


def _build_words(candidate: Any, text: str) -> list[OcrWord]:
    """Per-word boxes via `boundingBoxForRange_error_`, dropping unusable spans."""
    words: list[OcrWord] = []
    for char_start, char_length in _word_spans(text):
        try:
            rect_observation, _error = candidate.boundingBoxForRange_error_(
                (char_start, char_length), None
            )
        except Exception:  # a span Vision will not measure is simply unavailable
            continue
        if rect_observation is None:
            continue
        box = _box_from_quad(rect_observation)
        if box is None:
            continue
        words.append(
            OcrWord(
                text=text[char_start : char_start + char_length],
                char_start=char_start,
                char_length=char_length,
                box=box,
            )
        )
    return words
