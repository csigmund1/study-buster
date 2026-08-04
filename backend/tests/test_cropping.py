"""Unit tests for deterministic crop derivation (no API calls)."""

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.diagram_detection.cropping import FULL_PAGE, derive_crop
from tests.conftest import _box


def _light_page_with_ink(tmp_path: Path, size: tuple[int, int] = (400, 400)) -> Path:
    """A light-background page with an ink rectangle roughly in the middle third,
    surrounded by whitespace gutters wide enough to trigger the gap-snap."""
    width, height = size
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    # Ink block spanning the middle third of the page, well clear of the edges.
    draw.rectangle(
        (width * 0.3, height * 0.3, width * 0.7, height * 0.7),
        fill=0,
    )
    path = tmp_path / "page.png"
    image.convert("RGB").save(path)
    return path


def test_derive_crop_expands_seed_to_ink_gutter(tmp_path: Path) -> None:
    page = _light_page_with_ink(tmp_path)
    # Seed label box sits inside the ink block, smaller than the full ink region.
    label_boxes = [_box(0.45, 0.45, 0.1, 0.1)]

    crop = derive_crop(page, label_boxes, hint=None)

    # The crop should expand outward to roughly the ink block's extent (~0.3-0.7),
    # not stay pinned to the tiny seed box.
    assert crop.left < 0.4
    assert crop.top < 0.4
    assert crop.left + crop.width > 0.6
    assert crop.top + crop.height > 0.6
    # And should not swallow the entire page.
    assert crop.width < 0.9
    assert crop.height < 0.9


def test_derive_crop_falls_back_to_full_page_on_dark_background(tmp_path: Path) -> None:
    width, height = 300, 300
    image = Image.new("RGB", (width, height), (10, 10, 10))  # dark background
    path = tmp_path / "dark.png"
    image.save(path)

    label_boxes = [_box(0.4, 0.4, 0.1, 0.1)]
    crop = derive_crop(path, label_boxes, hint=None)

    assert crop == FULL_PAGE


def test_derive_crop_falls_back_to_full_page_when_crop_too_large(tmp_path: Path) -> None:
    width, height = 300, 300
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    # Ink covering almost the entire page -- whitespace-snap would expand the
    # crop to cover nearly everything.
    draw.rectangle((5, 5, width - 5, height - 5), fill=0)
    path = tmp_path / "full_ink.png"
    image.convert("RGB").save(path)

    label_boxes = [_box(0.1, 0.1, 0.05, 0.05), _box(0.85, 0.85, 0.05, 0.05)]
    crop = derive_crop(path, label_boxes, hint=None)

    assert crop == FULL_PAGE


def test_derive_crop_returns_full_page_when_no_labels(tmp_path: Path) -> None:
    page = _light_page_with_ink(tmp_path)
    assert derive_crop(page, [], hint=None) == FULL_PAGE


def test_derive_crop_uses_hint_as_seed(tmp_path: Path) -> None:
    page = _light_page_with_ink(tmp_path)
    label_boxes = [_box(0.45, 0.45, 0.05, 0.05)]
    hint = _box(0.32, 0.32, 0.05, 0.05)

    crop = derive_crop(page, label_boxes, hint=hint)

    assert crop.left <= hint.left + 0.02
