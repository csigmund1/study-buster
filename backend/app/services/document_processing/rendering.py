"""PDF page rendering with PyMuPDF (plan.md §8 step 4)."""

from pathlib import Path

import pymupdf


def render_pages(pdf_path: Path, pages_dir: Path, max_edge_px: int) -> int:
    """Render every page of `pdf_path` to `pages_dir/page_{n}.png` (1-indexed).

    Each page is scaled so its long edge is approximately `max_edge_px` pixels.
    Returns the page count.
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
    return page_count


def extract_page_texts(pdf_path: Path) -> list[str]:
    """Return the selectable text for each page, in page order.

    Handwritten annotations will not appear here — this is supplemental context
    only; the model relies primarily on the rendered page images.
    """
    with pymupdf.open(pdf_path) as doc:
        return [doc[index].get_text() for index in range(doc.page_count)]
