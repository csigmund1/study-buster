"""Shared helper for encoding a rendered page image as an Anthropic image block."""

import base64
import io
from pathlib import Path

from anthropic.types import ImageBlockParam
from PIL import Image


def encode_page_image(image_path: Path, max_edge_px: int) -> ImageBlockParam:
    with Image.open(image_path) as raw_image:
        image = raw_image.convert("RGB")
    long_edge = max(image.width, image.height)
    if long_edge > max_edge_px:
        scale = max_edge_px / long_edge
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
