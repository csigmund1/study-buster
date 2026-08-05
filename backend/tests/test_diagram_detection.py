"""Unit tests for diagram detection, occlusion mapping, and composition."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.config import get_settings
from app.models import Box, Direction, Occlusion, OcclusionKind
from app.services.diagram_detection import (
    DetectionPage,
    MockDiagramDetector,
    get_diagram_detector,
)
from app.services.diagram_detection.anthropic import (
    AnthropicDiagramDetector,
    ClassifierResult,
    LabelGroup,
    _union_results,
)
from app.services.diagram_detection.compose import (
    HIGHLIGHT_BORDER,
    HIGHLIGHT_FILL,
    MASK_COLOR,
    compose_occlusion,
)
from app.services.diagram_detection.ocr import OcrEngine, OcrItem
from app.services.diagram_detection.schemas import DiagramDetection, LabelDetection
from app.services.pipeline import _build_identify_occlusions, _occlusion_is_valid
from tests.conftest import _box


def _label(text: str, label_box: Box) -> LabelDetection:
    return LabelDetection(text=text, label_box=label_box)


# --- factory + mock ---------------------------------------------------------


def test_factory_returns_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGRAM_DETECTOR", "mock")
    assert isinstance(get_diagram_detector(get_settings()), MockDiagramDetector)


def test_factory_returns_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGRAM_DETECTOR", "anthropic")
    assert isinstance(get_diagram_detector(get_settings()), AnthropicDiagramDetector)


def test_factory_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGRAM_DETECTOR", "nope")
    with pytest.raises(ValueError):
        get_diagram_detector(get_settings())


def test_mock_detector_labels_inside_diagram(tmp_path: Path) -> None:
    detection = MockDiagramDetector().detect(
        DetectionPage(page_number=1, image_path=tmp_path / "page_1.png")
    )
    assert detection.is_labeled_diagram
    assert detection.diagram_box is not None
    assert detection.labels
    crop = detection.diagram_box
    for label in detection.labels:
        box = label.label_box
        assert box.left >= crop.left
        assert box.top >= crop.top
        assert box.left + box.width <= crop.left + crop.width
        assert box.top + box.height <= crop.top + crop.height


# --- OCR coordinate conversion -----------------------------------------------


class _FakeOcrEngine:
    def __init__(self, items: list[OcrItem]) -> None:
        self._items = items

    def extract(self, image_path: Path) -> list[OcrItem]:
        return self._items


class _FakeRect:
    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.origin = SimpleNamespace(x=x, y=y)
        self.size = SimpleNamespace(width=width, height=height)


class _FakeCandidate:
    def __init__(self, text: str, confidence: float) -> None:
        self._text = text
        self._confidence = confidence

    def string(self) -> str:
        return self._text

    def confidence(self) -> float:
        return self._confidence

    def boundingBoxForRange_error_(  # noqa: N802 - mirrors the ObjC selector
        self, _range: tuple[int, int], _error: None
    ) -> tuple[None, None]:
        return None, None  # word geometry unavailable for this stub


class _FakeObservation:
    """Stands in for a `VNRecognizedTextObservation`."""

    def __init__(self, text: str, confidence: float, rect: _FakeRect) -> None:
        self._candidate = _FakeCandidate(text, confidence)
        self._rect = rect

    def topCandidates_(self, _count: int) -> list[_FakeCandidate]:  # noqa: N802
        return [self._candidate]

    def boundingBox(self) -> _FakeRect:  # noqa: N802
        return self._rect


def test_vision_observation_flips_and_clamps() -> None:
    """Vision is bottom-left origin; `Box` is top-left. The flip is `1 - y - h`."""
    from app.services.diagram_detection.ocr import _build_item

    # normal item: bottom-left (x, y, w, h) -> top-left flip
    thyroid = _build_item(_FakeObservation("Thyroid", 0.9, _FakeRect(0.1, 0.2, 0.3, 0.1)))
    assert thyroid is not None
    assert thyroid.box.left == pytest.approx(0.1)
    assert thyroid.box.top == pytest.approx(0.7)  # 1 - 0.2 - 0.1
    assert thyroid.box.width == pytest.approx(0.3)
    assert thyroid.box.height == pytest.approx(0.1)
    assert thyroid.confidence == pytest.approx(0.9)

    # zero-size item after clamping is dropped, never raised
    assert _build_item(_FakeObservation("Tiny", 0.5, _FakeRect(1.0, 1.0, 0.0, 0.0))) is None

    # out-of-range coordinates clamp into [0, 1]
    edge = _build_item(_FakeObservation("Edge", 0.7, _FakeRect(-0.05, -0.05, 0.2, 0.2)))
    assert edge is not None
    assert 0.0 <= edge.box.left <= 1.0
    assert 0.0 <= edge.box.top <= 1.0

    # a line whose word ranges Vision will not measure still yields the line item
    assert edge.words == []
    assert 0.0 <= edge.box.top <= 1.0


# --- classifier union rule ---------------------------------------------------


def test_union_results_dedupes_identical_sets() -> None:
    passes = [
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0, 1], label_text="First")],
        ),
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0, 1], label_text="Second")],
        ),
    ]
    is_labeled_diagram, _diagram_box, groups = _union_results(passes, num_items=5)
    assert is_labeled_diagram
    assert len(groups) == 1
    (indices, label_text) = groups[0]
    assert indices == frozenset({0, 1})
    assert label_text == "First"  # first occurrence wins


def test_union_results_drops_strict_subsets() -> None:
    passes = [
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0], label_text="Solo")],
        ),
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0, 1], label_text="Combined")],
        ),
    ]
    _is_labeled_diagram, _diagram_box, groups = _union_results(passes, num_items=5)
    assert len(groups) == 1
    assert groups[0][0] == frozenset({0, 1})


def test_union_results_filters_out_of_range_indices() -> None:
    passes = [
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0, 99], label_text="Partial")],
        ),
    ]
    _is_labeled_diagram, _diagram_box, groups = _union_results(passes, num_items=3)
    assert groups == [(frozenset({0}), "Partial")]


def test_union_results_is_labeled_diagram_is_or() -> None:
    passes = [
        ClassifierResult(is_labeled_diagram=False, labels=[]),
        ClassifierResult(is_labeled_diagram=True, labels=[]),
    ]
    is_labeled_diagram, _diagram_box, _groups = _union_results(passes, num_items=0)
    assert is_labeled_diagram


def test_detector_unions_two_passes_and_builds_label_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        OcrItem(text="Thyroid", box=_box(0.1, 0.1, 0.1, 0.05)),
        OcrItem(text="gland", box=_box(0.2, 0.1, 0.08, 0.05)),
        OcrItem(text="Title", box=_box(0.0, 0.0, 0.3, 0.05)),
    ]
    ocr: OcrEngine = _FakeOcrEngine(items)

    detector = AnthropicDiagramDetector(model="claude-haiku-4-5", max_edge_px=1024, ocr=ocr)
    detector._encode_page = MagicMock(return_value={"type": "image", "source": {}})  # type: ignore[method-assign]

    pass_results = [
        ClassifierResult(
            is_labeled_diagram=True,
            labels=[LabelGroup(item_indices=[0, 1], label_text="Thyroid gland")],
        ),
        ClassifierResult(
            is_labeled_diagram=False,
            labels=[LabelGroup(item_indices=[0], label_text="Thyroid")],
        ),
    ]
    detector._classify = MagicMock(side_effect=pass_results)  # type: ignore[method-assign]

    detection = detector.detect(DetectionPage(page_number=1, image_path=Path("dummy.png")))

    assert detection.is_labeled_diagram
    assert len(detection.labels) == 1
    label = detection.labels[0]
    assert label.text == "Thyroid gland"
    # union of items[0] and items[1] boxes
    assert label.label_box.left == pytest.approx(0.1)
    assert label.label_box.top == pytest.approx(0.1)
    assert label.label_box.width == pytest.approx(0.18)
    assert label.label_box.height == pytest.approx(0.05)


def test_detector_returns_empty_when_no_ocr_items() -> None:
    detector = AnthropicDiagramDetector(
        model="claude-haiku-4-5", max_edge_px=1024, ocr=_FakeOcrEngine([])
    )
    detection = detector.detect(DetectionPage(page_number=1, image_path=Path("dummy.png")))
    assert detection.is_labeled_diagram is False
    assert detection.labels == []


# --- occlusion mapping + geometry validation --------------------------------


def _detection(labels: list[LabelDetection], *, is_diagram: bool = True) -> DiagramDetection:
    return DiagramDetection(
        is_labeled_diagram=is_diagram,
        diagram_box=_box(0.1, 0.1, 0.8, 0.8),
        labels=labels,
    )


def _light_page(tmp_path: Path) -> Path:
    page = tmp_path / "page.png"
    Image.new("RGB", (400, 400), "white").save(page)
    return page


def test_build_identify_one_occlusion_per_label(tmp_path: Path) -> None:
    labels = [
        _label("A", _box(0.2, 0.2, 0.1, 0.05)),
        _label("B", _box(0.2, 0.5, 0.1, 0.05)),
    ]
    occlusions = _build_identify_occlusions(_detection(labels), _light_page(tmp_path))
    assert len(occlusions) == 2
    assert all(occ.direction == Direction.IDENTIFY for occ in occlusions)
    # every label's box masks the question side, regardless of which is the target
    assert all(len(occ.mask_boxes) == 2 for occ in occlusions)


def test_build_identify_empty_when_not_a_diagram(tmp_path: Path) -> None:
    labels = [_label("A", _box(0.2, 0.2, 0.1, 0.05))]
    occlusions = _build_identify_occlusions(
        _detection(labels, is_diagram=False), _light_page(tmp_path)
    )
    assert occlusions == []


def test_build_identify_empty_when_no_labels(tmp_path: Path) -> None:
    occlusions = _build_identify_occlusions(
        DiagramDetection(is_labeled_diagram=True, diagram_box=None, labels=[]),
        _light_page(tmp_path),
    )
    assert occlusions == []


def test_occlusion_rejects_empty_label() -> None:
    occ = Occlusion(
        direction=Direction.IDENTIFY,
        label="   ",
        crop_box=_box(0.1, 0.1, 0.8, 0.8),
        label_box=_box(0.2, 0.2, 0.1, 0.05),
        mask_boxes=[_box(0.2, 0.2, 0.1, 0.05)],
    )
    assert not _occlusion_is_valid(occ)


# --- composition ------------------------------------------------------------


def test_compose_occlusion_writes_two_cropped_images(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    Image.new("RGB", (400, 300), "white").save(page)

    occ = Occlusion(
        kind=OcclusionKind.DIAGRAM,
        direction=Direction.IDENTIFY,
        labels=["Target"],
        crop_box=_box(0.1, 0.1, 0.5, 0.5),
        target_boxes=[_box(0.15, 0.15, 0.1, 0.05)],
        mask_boxes=[_box(0.15, 0.15, 0.1, 0.05), _box(0.35, 0.35, 0.1, 0.05)],
    )
    question = tmp_path / "q.png"
    answer = tmp_path / "a.png"
    compose_occlusion(page, occ, question, answer)

    assert question.is_file() and answer.is_file()
    with Image.open(question) as q_img, Image.open(answer) as a_img:
        assert q_img.size == a_img.size
        # cropped to ~half the page in each dimension
        assert 180 <= q_img.size[0] <= 220
        assert 130 <= q_img.size[1] <= 170

        q_rgb = q_img.convert("RGB")
        a_rgb = a_img.convert("RGB")

        # question: the single target is highlighted (amber fill somewhere inside it)
        target_px = q_rgb.getpixel((15, 8))
        assert target_px == HIGHLIGHT_FILL or _near_border(q_rgb, occ.target_boxes[0])

        # question: the other mask_box is plain gray mask
        # mask_boxes[1] = (0.35, 0.35, 0.1, 0.05) page-normalized; crop_box starts
        # at (0.1, 0.1) over a 400x300 page -> crop-local (100, 75) to (140, 90).
        other_px = q_rgb.getpixel((110, 80))
        assert other_px == MASK_COLOR

        # answer: the target is revealed (original white page), the other still masked
        assert a_rgb.getpixel((15, 8)) == (255, 255, 255)
        assert a_rgb.getpixel((110, 80)) == MASK_COLOR


def test_compose_occlusion_text_kind_masks_without_highlighting(tmp_path: Path) -> None:
    """Text cards get no pointer: the span is masked flat, never highlighted."""
    page = tmp_path / "page.png"
    Image.new("RGB", (400, 300), "white").save(page)

    occ = Occlusion(
        kind=OcclusionKind.TEXT,
        direction=Direction.IDENTIFY,
        labels=["Part of"],
        crop_box=_box(0.0, 0.0, 1.0, 1.0),  # text cards use the full page
        target_boxes=[_box(0.15, 0.15, 0.1, 0.05)],
        mask_boxes=[_box(0.15, 0.15, 0.1, 0.05)],
    )
    question = tmp_path / "q.png"
    answer = tmp_path / "a.png"
    compose_occlusion(page, occ, question, answer)

    with Image.open(question) as q_img, Image.open(answer) as a_img:
        q_rgb = q_img.convert("RGB")
        a_rgb = a_img.convert("RGB")

        # full page, uncropped
        assert q_rgb.size == (400, 300)

        # question: the span is plain mask, NOT the highlight style
        inside = q_rgb.getpixel((70, 50))
        assert inside == MASK_COLOR
        assert inside != HIGHLIGHT_FILL
        assert HIGHLIGHT_FILL not in set(q_rgb.getdata())
        assert HIGHLIGHT_BORDER not in set(q_rgb.getdata())

        # question: pixels outside the masked span are untouched
        assert q_rgb.getpixel((300, 250)) == (255, 255, 255)
        assert q_rgb.getpixel((10, 10)) == (255, 255, 255)

        # answer: the span is revealed again
        assert a_rgb.getpixel((70, 50)) == (255, 255, 255)

        # answer: target box reveals original page content (white), not masked/highlighted
        answer_target_px = a_rgb.getpixel((15, 8))
        assert answer_target_px != MASK_COLOR
        assert answer_target_px != HIGHLIGHT_FILL

    assert question.read_bytes() != answer.read_bytes()


def _near_border(image: Image.Image, box: Box) -> bool:
    # Fallback check: scan the label_box region for the highlight border color.
    width, height = image.size
    x0 = int(box.left * width * 0.5)
    y0 = int(box.top * height * 0.5)
    x1 = int((box.left + box.width) * width * 0.5)
    y1 = int((box.top + box.height) * height * 0.5)
    for y in range(y0, min(y1 + 1, height)):
        for x in range(x0, min(x1 + 1, width)):
            if image.getpixel((x, y)) == HIGHLIGHT_BORDER:
                return True
    return False
