from pathlib import Path

import pymupdf

from app.services.document_processing import extract_page_texts, render_pages


def _build_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=612, height=792)
        if index != 1:
            # Leave the middle page with no selectable text, to exercise the
            # "handwriting won't appear as text" case.
            page.insert_text((72, 72), f"Slide {index + 1} content")
    pdf_path = tmp_path / "lecture.pdf"
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_render_pages_writes_one_png_per_page(tmp_path: Path) -> None:
    pdf_path = _build_pdf(tmp_path)
    pages_dir = tmp_path / "pages"

    page_count = render_pages(pdf_path, pages_dir, max_edge_px=400)

    assert page_count == 3
    for page_number in (1, 2, 3):
        image_path = pages_dir / f"page_{page_number}.png"
        assert image_path.is_file()
        assert image_path.read_bytes().startswith(b"\x89PNG")


def test_render_pages_scales_long_edge_toward_target(tmp_path: Path) -> None:
    pdf_path = _build_pdf(tmp_path)
    pages_dir = tmp_path / "pages"

    render_pages(pdf_path, pages_dir, max_edge_px=400)

    pixmap = pymupdf.Pixmap(str(pages_dir / "page_1.png"))
    long_edge = max(pixmap.width, pixmap.height)
    # 612x792 scaled so the long edge (792) is ~400px.
    assert 380 <= long_edge <= 420


def test_extract_page_texts_returns_text_per_page_with_blanks_allowed(tmp_path: Path) -> None:
    pdf_path = _build_pdf(tmp_path)

    texts = extract_page_texts(pdf_path)

    assert len(texts) == 3
    assert "Slide 1 content" in texts[0]
    assert texts[1].strip() == ""
    assert "Slide 3 content" in texts[2]
