"""PDF page rendering with PyMuPDF (plan.md §8 step 4)."""

from collections.abc import Callable
from pathlib import Path

import pymupdf


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
