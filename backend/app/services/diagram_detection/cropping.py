"""Deterministic crop-region derivation for diagram cards.

No API calls: a pure function of the rendered page image and the label boxes
detected on it. Seeds a rectangle from the label boxes (plus an optional rough
model hint), pads it, then expands each edge outward to the nearest whitespace
gutter so the crop doesn't clip diagram artwork tight against the label text.
"""

from pathlib import Path

from PIL import Image

from app.models.occlusion import Box

INK_THRESHOLD = 230  # grayscale value below which a pixel counts as "ink"
LIGHT_BACKGROUND_MEDIAN = 128
SEED_PAD_FRACTION = 0.03
MIN_GAP_PX = 8
GAP_FRACTION = 0.015
MAX_CROP_AREA_FRACTION = 0.90

FULL_PAGE = Box(left=0, top=0, width=1, height=1)


def _union_box(boxes: list[Box]) -> Box:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.left + box.width for box in boxes)
    bottom = max(box.top + box.height for box in boxes)
    return Box(left=left, top=top, width=right - left, height=bottom - top)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def derive_crop(page_image_path: Path, label_boxes: list[Box], hint: Box | None) -> Box:
    """Return the page-normalized crop region for a diagram card.

    Falls back to the full page if `label_boxes` is empty, the background
    isn't light enough for whitespace-snapping to be reliable, or the derived
    crop would cover more than `MAX_CROP_AREA_FRACTION` of the page.
    """
    if not label_boxes:
        return FULL_PAGE

    seed_boxes = list(label_boxes)
    if hint is not None:
        seed_boxes.append(hint)
    seed = _union_box(seed_boxes)

    with Image.open(page_image_path) as raw_image:
        gray = raw_image.convert("L")
    page_width, page_height = gray.size

    histogram = gray.histogram()
    total_pixels = page_width * page_height
    if total_pixels == 0:
        return FULL_PAGE
    median_luminance = _median_from_histogram(histogram, total_pixels)
    if median_luminance < LIGHT_BACKGROUND_MEDIAN:
        return FULL_PAGE

    # Ink mask: True where the pixel is "ink" (below threshold).
    ink = gray.point(lambda p: 255 if p < INK_THRESHOLD else 0)
    row_has_ink, col_has_ink = _row_col_ink(ink, page_width, page_height)

    pad_x = round(SEED_PAD_FRACTION * page_width)
    pad_y = round(SEED_PAD_FRACTION * page_height)
    left_px = round(seed.left * page_width) - pad_x
    top_px = round(seed.top * page_height) - pad_y
    right_px = round((seed.left + seed.width) * page_width) + pad_x
    bottom_px = round((seed.top + seed.height) * page_height) + pad_y

    left_px = max(0, min(left_px, page_width - 1))
    right_px = max(left_px + 1, min(right_px, page_width))
    top_px = max(0, min(top_px, page_height - 1))
    bottom_px = max(top_px + 1, min(bottom_px, page_height))

    gap_x = max(MIN_GAP_PX, round(GAP_FRACTION * page_width))
    gap_y = max(MIN_GAP_PX, round(GAP_FRACTION * page_height))

    left_px = _expand_left(col_has_ink, left_px, gap_x)
    right_px = _expand_right(col_has_ink, right_px, gap_x, page_width)
    top_px = _expand_up(row_has_ink, top_px, gap_y)
    bottom_px = _expand_down(row_has_ink, bottom_px, gap_y, page_height)

    crop_left = _clamp(left_px / page_width, 0.0, 1.0)
    crop_top = _clamp(top_px / page_height, 0.0, 1.0)
    crop_width = _clamp((right_px - left_px) / page_width, 0.0, 1.0 - crop_left)
    crop_height = _clamp((bottom_px - top_px) / page_height, 0.0, 1.0 - crop_top)

    if crop_width <= 0 or crop_height <= 0:
        return FULL_PAGE

    if (crop_width * crop_height) > MAX_CROP_AREA_FRACTION:
        return FULL_PAGE

    return Box(left=crop_left, top=crop_top, width=crop_width, height=crop_height)


def _median_from_histogram(histogram: list[int], total_pixels: int) -> int:
    target = total_pixels // 2
    running = 0
    for value, count in enumerate(histogram[:256]):
        running += count
        if running >= target:
            return value
    return 255


def _row_col_ink(ink_image: Image.Image, width: int, height: int) -> tuple[list[bool], list[bool]]:
    """Compute per-row and per-column "has any ink" booleans via row/column
    bounding boxes (fast: relies on Pillow's C-level `getbbox`, no per-pixel
    Python loop over the whole page)."""
    row_has_ink = [False] * height
    col_has_ink = [False] * width

    for y in range(height):
        row = ink_image.crop((0, y, width, y + 1))
        if row.getbbox() is not None:
            row_has_ink[y] = True

    for x in range(width):
        col = ink_image.crop((x, 0, x + 1, height))
        if col.getbbox() is not None:
            col_has_ink[x] = True

    return row_has_ink, col_has_ink


def _expand_left(col_has_ink: list[bool], start: int, gap: int) -> int:
    x = start
    blank_run = 0
    while x > 0:
        x -= 1
        if col_has_ink[x]:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= gap:
                return x + blank_run
    return 0


def _expand_right(col_has_ink: list[bool], start: int, gap: int, width: int) -> int:
    x = start
    blank_run = 0
    while x < width:
        if col_has_ink[x]:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= gap:
                return x + 1 - blank_run
        x += 1
    return width


def _expand_up(row_has_ink: list[bool], start: int, gap: int) -> int:
    y = start
    blank_run = 0
    while y > 0:
        y -= 1
        if row_has_ink[y]:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= gap:
                return y + blank_run
    return 0


def _expand_down(row_has_ink: list[bool], start: int, gap: int, height: int) -> int:
    y = start
    blank_run = 0
    while y < height:
        if row_has_ink[y]:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= gap:
                return y + 1 - blank_run
        y += 1
    return height
