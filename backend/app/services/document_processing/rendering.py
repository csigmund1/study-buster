"""PDF page rendering with PyMuPDF (plan.md §8 step 4)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.models.occlusion import Box

#: PyMuPDF span-flag bits for typographic style (see `get_text("dict")`).
_FLAG_ITALIC = 1 << 1  # 2
_FLAG_BOLD = 1 << 4  # 16


@dataclass(frozen=True)
class StyledSpan:
    """One PDF text span with its typographic style and page-normalized box.

    Geometry is normalized to `[0, 1]`, origin top-left, so it lines up with the
    OCR boxes (`OcrItem.box`) a text occlusion is matched against.
    """

    text: str
    box: Box
    bold: bool
    italic: bool


def render_pages(
    pdf_path: Path,
    pages_dir: Path,
    max_edge_px: int,
    on_page: Callable[[], None] | None = None,
) -> int:
    """Render every page of `pdf_path` to `pages_dir/page_{n}.png` (1-indexed).

    Each page is scaled so its long edge is approximately `max_edge_px` pixels.
    `on_page`, when given, is called once after each page is written (progress
    reporting). Returns the page count.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
        for index in range(page_count):
            page = doc[index]
            long_edge = max(page.rect.width, page.rect.height)
            zoom = max_edge_px / long_edge if long_edge > 0 else 1.0
            matrix = pymupdf.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix)
            pixmap.save(pages_dir / f"page_{index + 1}.png")
            if on_page is not None:
                on_page()
    return page_count


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalized_box(
    x0: float, y0: float, x1: float, y1: float, width: float, height: float
) -> Box | None:
    """Map a PyMuPDF span bbox (points, top-left origin) into a normalized `Box`.

    Returns `None` for a degenerate span (zero area after clamping), which `Box`
    would reject — such a span carries no usable geometry.
    """
    left = _clamp01(x0 / width)
    right = _clamp01(x1 / width)
    top = _clamp01(y0 / height)
    bottom = _clamp01(y1 / height)
    if right - left <= 0 or bottom - top <= 0:
        return None
    return Box(left=left, top=top, width=right - left, height=bottom - top)


def _is_bold(flags: int, font: str) -> bool:
    return bool(flags & _FLAG_BOLD) or "bold" in font.lower()


def _is_italic(flags: int, font: str) -> bool:
    lowered = font.lower()
    return bool(flags & _FLAG_ITALIC) or "italic" in lowered or "oblique" in lowered


def extract_page_styles(pdf_path: Path) -> list[list[StyledSpan]]:
    """Return the styled text spans for each page, in page order.

    Uses PyMuPDF's `get_text("dict")` to recover per-span typography (bold /
    italic via flag bits or the font name) and geometry, normalized to `[0, 1]`.
    This is what lets text occlusion prioritize the lecturer's own emphasis —
    OCR alone carries no font style. Handwritten annotations have no PDF span
    and simply contribute nothing here.
    """
    pages: list[list[StyledSpan]] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            width = page.rect.width or 1.0
            height = page.rect.height or 1.0
            spans: list[StyledSpan] = []
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = str(span.get("text", ""))
                        if not text.strip():
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        box = _normalized_box(x0, y0, x1, y1, width, height)
                        if box is None:
                            continue
                        flags = int(span.get("flags", 0))
                        font = str(span.get("font", ""))
                        spans.append(
                            StyledSpan(
                                text=text,
                                box=box,
                                bold=_is_bold(flags, font),
                                italic=_is_italic(flags, font),
                            )
                        )
            pages.append(spans)
    return pages


def extract_page_texts(pdf_path: Path, on_page: Callable[[], None] | None = None) -> list[str]:
    """Return the selectable text for each page, in page order.

    Handwritten annotations will not appear here — this is supplemental context
    only; the model relies primarily on the rendered page images. `on_page`, when
    given, is called once after each page is read (progress reporting).
    """
    texts: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            texts.append(doc[index].get_text())
            if on_page is not None:
                on_page()
    return texts
