"""Occlusion image composition, shared by diagram and text-occlusion cards.

Crops the card region out of a rendered page and burns in the occlusion masks:
the question image hides every box in `mask_boxes`; the answer image hides them
all *except* `target_boxes`, revealing the answer in place.

A single-target diagram card additionally draws its target in the highlight
style — that mask, sitting at the end of the label's arrow/leader line, is the
pointer for "what is this?". Grouped diagram cards and every text card get no
highlight: there is no single thing to point at.
"""

from pathlib import Path

from PIL import Image, ImageDraw

from app.models.occlusion import Box, Occlusion, OcclusionKind

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
    skip_boxes: list[Box],
    highlight_box: Box | None,
    page_width: int,
    page_height: int,
    x0: float,
    y0: float,
) -> Image.Image:
    out = crop.copy()
    draw = ImageDraw.Draw(out)
    for box in mask_boxes:
        if box in skip_boxes:
            continue
        rect = _crop_local_rect(box, x0, y0, page_width, page_height)
        draw.rectangle(rect, fill=MASK_COLOR)
    if highlight_box is not None:
        rect = _crop_local_rect(highlight_box, x0, y0, page_width, page_height)
        draw.rectangle(
            rect, fill=HIGHLIGHT_FILL, outline=HIGHLIGHT_BORDER, width=HIGHLIGHT_BORDER_WIDTH
        )
    return out


def _pointer_box(occlusion: Occlusion) -> Box | None:
    """The single target to draw in the highlight style, if there is one.

    Only a single-target diagram card has a pointer. Grouped diagram cards and
    all text cards return `None` — with several targets there is nothing
    unambiguous to point at.
    """
    if occlusion.kind is OcclusionKind.DIAGRAM and len(occlusion.target_boxes) == 1:
        return occlusion.target_boxes[0]
    return None


def compose_occlusion(
    page_image_path: Path, occlusion: Occlusion, question_out: Path, answer_out: Path
) -> None:
    """Render the question/answer images for `occlusion`.

    Question: every box in `mask_boxes` is filled, then a single-target diagram's
    target is drawn over its own mask in the highlight style. Answer: every box
    in `mask_boxes` except `target_boxes` is filled, revealing the answer.
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
        [],
        _pointer_box(occlusion),
        page_width,
        page_height,
        x0,
        y0,
    )
    answer = _draw_masked(
        crop,
        occlusion.mask_boxes,
        occlusion.target_boxes,
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
