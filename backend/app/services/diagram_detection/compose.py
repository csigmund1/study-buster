"""Identify-direction image composition.

Crops the diagram region out of a rendered page and burns in the occlusion
masks: the question image hides every label and highlights the target label's
own mask (the pointer — "what is this?"); the answer image hides every label
except the target's, so its text is revealed.
"""

from pathlib import Path

from PIL import Image, ImageDraw

from app.models.occlusion import Box, Occlusion

MASK_COLOR = (60, 60, 60)
HIGHLIGHT_FILL = (255, 213, 79)
HIGHLIGHT_BORDER = (230, 150, 0)
HIGHLIGHT_BORDER_WIDTH = 3

PixelRect = tuple[float, float, float, float]


def _crop_local_rect(box: Box, x0: float, y0: float, width: float, height: float) -> PixelRect:
    """Convert a page-normalized `Box` to a crop-local pixel rect."""
    return (
        box.left * width - x0,
        box.top * height - y0,
        (box.left + box.width) * width - x0,
        (box.top + box.height) * height - y0,
    )


def _draw_masked(
    crop: Image.Image,
    mask_boxes: list[Box],
    skip_box: Box | None,
    highlight_box: Box | None,
    page_width: int,
    page_height: int,
    x0: float,
    y0: float,
) -> Image.Image:
    out = crop.copy()
    draw = ImageDraw.Draw(out)
    for box in mask_boxes:
        if skip_box is not None and box == skip_box:
            continue
        rect = _crop_local_rect(box, x0, y0, page_width, page_height)
        draw.rectangle(rect, fill=MASK_COLOR)
    if highlight_box is not None:
        rect = _crop_local_rect(highlight_box, x0, y0, page_width, page_height)
        draw.rectangle(
            rect, fill=HIGHLIGHT_FILL, outline=HIGHLIGHT_BORDER, width=HIGHLIGHT_BORDER_WIDTH
        )
    return out


def compose_identify(
    page_image_path: Path, occlusion: Occlusion, question_out: Path, answer_out: Path
) -> None:
    """Render the identify-direction question/answer images for `occlusion`.

    The question image masks every label in `occlusion.mask_boxes` except the
    target's, which is drawn in the highlight style (the pointer). The answer
    image masks every label except the target's, revealing its text with no
    highlight.
    """
    with Image.open(page_image_path) as raw_page_image:
        page_image = raw_page_image.convert("RGB")
    page_width, page_height = page_image.size

    crop_box = occlusion.crop_box
    x0 = crop_box.left * page_width
    y0 = crop_box.top * page_height
    x1 = (crop_box.left + crop_box.width) * page_width
    y1 = (crop_box.top + crop_box.height) * page_height
    crop = page_image.crop((x0, y0, x1, y1))

    question = _draw_masked(
        crop,
        occlusion.mask_boxes,
        occlusion.label_box,
        occlusion.label_box,
        page_width,
        page_height,
        x0,
        y0,
    )
    answer = _draw_masked(
        crop,
        occlusion.mask_boxes,
        occlusion.label_box,
        None,
        page_width,
        page_height,
        x0,
        y0,
    )

    question_out.parent.mkdir(parents=True, exist_ok=True)
    answer_out.parent.mkdir(parents=True, exist_ok=True)
    question.save(question_out, format="PNG")
    answer.save(answer_out, format="PNG")
