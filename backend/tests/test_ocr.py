"""Direct-Vision OCR: parity with the previous `ocrmac` path, plus range geometry.

The parity tests are the guard rail for the OCR rewrite: diagram detection
consumes these line boxes, so any drift silently changes which items the
classifier sees. They need macOS Vision and the sample pages, and skip
elsewhere.
"""

from pathlib import Path

import pytest

from app.models.occlusion import Box
from app.services.diagram_detection.ocr import (
    AppleVisionOcr,
    OcrItem,
    _box_from_bottom_left,
    _box_from_quad,
    _word_spans,
    union_boxes,
)

SPIKE_INPUTS = Path(__file__).resolve().parents[2] / "spikes" / "inputs"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _vision_available() -> bool:
    try:
        import objc  # noqa: F401
        import Vision  # noqa: F401
    except Exception:
        return False
    return True


def _ocrmac_available() -> bool:
    try:
        from ocrmac import ocrmac  # noqa: F401
    except Exception:
        return False
    return True


requires_vision = pytest.mark.skipif(
    not _vision_available(), reason="macOS Vision (pyobjc) unavailable"
)
requires_samples = pytest.mark.skipif(
    not (SPIKE_INPUTS / "test_notes_p01.png").is_file(),
    reason="sample pages not present",
)


# --- pure geometry (no Vision needed) ---------------------------------------


def test_word_spans_tracks_character_offsets() -> None:
    assert _word_spans("Overview of Abdomen") == [(0, 8), (9, 2), (12, 7)]
    assert _word_spans("  leading and trailing  ") == [(2, 7), (10, 3), (14, 8)]
    assert _word_spans("") == []
    assert _word_spans("   ") == []


def test_bottom_left_conversion_flips_the_origin() -> None:
    box = _box_from_bottom_left(0.1, 0.7, 0.2, 0.1)
    assert box is not None
    assert box.left == pytest.approx(0.1)
    # top = 1 - y - height
    assert box.top == pytest.approx(0.2)
    assert box.width == pytest.approx(0.2)
    assert box.height == pytest.approx(0.1)


def test_degenerate_geometry_is_dropped_not_raised() -> None:
    # `Box` rejects zero/negative size; these must return None instead of raising.
    assert _box_from_bottom_left(0.5, 0.5, 0.0, 0.1) is None
    assert _box_from_bottom_left(0.5, 0.5, 0.1, 0.0) is None
    assert _box_from_bottom_left(2.0, 2.0, 0.1, 0.1) is None


class _FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class _FakeQuad:
    """Stands in for a `VNRectangleObservation` (four corners, bottom-left origin)."""

    def __init__(self, left: float, right: float, bottom: float, top: float) -> None:
        self._corners = {
            "bottomLeft": _FakePoint(left, bottom),
            "bottomRight": _FakePoint(right, bottom),
            "topLeft": _FakePoint(left, top),
            "topRight": _FakePoint(right, top),
        }

    def bottomLeft(self) -> _FakePoint:  # noqa: N802 - mirrors the ObjC selector
        return self._corners["bottomLeft"]

    def bottomRight(self) -> _FakePoint:  # noqa: N802
        return self._corners["bottomRight"]

    def topLeft(self) -> _FakePoint:  # noqa: N802
        return self._corners["topLeft"]

    def topRight(self) -> _FakePoint:  # noqa: N802
        return self._corners["topRight"]


def test_quad_conversion_normalizes_into_unit_space() -> None:
    box = _box_from_quad(_FakeQuad(left=0.2, right=0.5, bottom=0.6, top=0.75))
    assert box is not None
    assert box.left == pytest.approx(0.2)
    assert box.top == pytest.approx(0.25)  # 1 - max(y)
    assert box.width == pytest.approx(0.3)
    assert box.height == pytest.approx(0.15)
    assert 0.0 <= box.left <= 1.0 and 0.0 <= box.top <= 1.0


def test_degenerate_quad_is_dropped() -> None:
    assert _box_from_quad(_FakeQuad(left=0.4, right=0.4, bottom=0.5, top=0.6)) is None
    assert _box_from_quad(_FakeQuad(left=0.2, right=0.5, bottom=0.5, top=0.5)) is None


def test_union_boxes_covers_every_input() -> None:
    boxes = [
        Box(left=0.1, top=0.2, width=0.1, height=0.05),
        Box(left=0.3, top=0.25, width=0.1, height=0.05),
    ]
    merged = union_boxes(boxes)
    assert merged is not None
    assert merged.left == pytest.approx(0.1)
    assert merged.top == pytest.approx(0.2)
    assert merged.width == pytest.approx(0.3)
    assert merged.height == pytest.approx(0.1)
    assert union_boxes([]) is None


def test_ocr_item_defaults_keep_older_engines_valid() -> None:
    """Fake engines in other tests construct `OcrItem(text=..., box=...)`."""
    item = OcrItem(text="x", box=Box(left=0.1, top=0.1, width=0.1, height=0.1))
    assert item.confidence == 1.0
    assert item.words == []


# --- live Vision -------------------------------------------------------------


@requires_vision
@requires_samples
@pytest.mark.skipif(not _ocrmac_available(), reason="ocrmac unavailable")
@pytest.mark.parametrize("page", ["p01", "p05", "p07"])
def test_line_boxes_match_the_previous_ocrmac_output(page: str) -> None:
    """Byte-for-byte parity with the geometry diagram detection used to receive.

    Measured in the plan's investigation phase: p01 32 lines / 89 words,
    p05 33/69, p07 24/61.
    """
    from ocrmac import ocrmac

    image_path = SPIKE_INPUTS / f"test_notes_{page}.png"

    legacy: list[tuple[str, tuple[float, float, float, float]]] = []
    for text, _conf, bbox in ocrmac.OCR(str(image_path), recognition_level="accurate").recognize():
        x, y, width, height = bbox
        left = max(0.0, min(1.0, x))
        right = max(0.0, min(1.0, x + width))
        top = max(0.0, min(1.0, 1 - y - height))
        bottom = max(0.0, min(1.0, 1 - y))
        if right - left <= 0 or bottom - top <= 0:
            continue
        legacy.append((text, (left, top, right - left, bottom - top)))

    items = AppleVisionOcr().extract(image_path)

    assert len(items) == len(legacy)
    for (legacy_text, legacy_box), item in zip(legacy, items):
        assert item.text == legacy_text
        assert item.box.left == pytest.approx(legacy_box[0], abs=1e-9)
        assert item.box.top == pytest.approx(legacy_box[1], abs=1e-9)
        assert item.box.width == pytest.approx(legacy_box[2], abs=1e-9)
        assert item.box.height == pytest.approx(legacy_box[3], abs=1e-9)


@requires_vision
@requires_samples
def test_p01_first_line_matches_the_recorded_measurement() -> None:
    """The plan's §2 checkpoint value, pinned so the rewrite cannot drift."""
    items = AppleVisionOcr().extract(SPIKE_INPUTS / "test_notes_p01.png")

    assert items
    first = items[0]
    assert first.text == "Overview of Abdomen"
    assert first.box.left == pytest.approx(0.023, abs=5e-4)
    assert first.box.top == pytest.approx(0.064, abs=5e-4)
    assert first.box.width == pytest.approx(0.410, abs=5e-4)
    assert first.box.height == pytest.approx(0.054, abs=5e-4)


@requires_vision
@requires_samples
def test_word_boxes_are_produced_and_union_to_the_line() -> None:
    items = AppleVisionOcr().extract(SPIKE_INPUTS / "test_notes_p01.png")
    first = next(item for item in items if item.text == "Overview of Abdomen")

    assert [word.text for word in first.words] == ["Overview", "of", "Abdomen"]
    for word in first.words:
        assert first.text[word.char_start : word.char_start + word.char_length] == word.text

    # A phrase mask is one tight rect: the union of its word boxes sits within
    # the line box rather than spilling outside it.
    merged = union_boxes([word.box for word in first.words])
    assert merged is not None
    assert merged.left >= first.box.left - 1e-3
    assert merged.top >= first.box.top - 1e-3
    assert merged.left + merged.width <= first.box.left + first.box.width + 1e-3


@requires_vision
@requires_samples
def test_confidence_separates_print_from_handwriting() -> None:
    """Printed headings score ~1.0; the handwriting band measured 0.30-0.50."""
    items = AppleVisionOcr().extract(SPIKE_INPUTS / "test_notes_p07.png")

    confidences = [item.confidence for item in items]
    assert confidences
    assert max(confidences) == pytest.approx(1.0, abs=1e-6)
    assert min(confidences) < 0.6
    assert all(0.0 <= value <= 1.0 for value in confidences)


@requires_vision
@requires_samples
def test_repeated_extract_is_cached_per_path() -> None:
    """One pass per page serves both features; a page is never OCR'd twice."""
    engine = AppleVisionOcr()
    image_path = SPIKE_INPUTS / "test_notes_p01.png"

    first = engine.extract(image_path)
    calls: list[Path] = []
    original = engine._recognize

    def counting_recognize(path: Path) -> list[OcrItem]:
        calls.append(path)
        return original(path)

    engine._recognize = counting_recognize  # type: ignore[method-assign]
    second = engine.extract(image_path)

    assert calls == []  # served from cache, no second recognition pass
    assert second is first
