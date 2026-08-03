"""File layout helpers for per-job storage.

Layout:
    DATA_DIR/jobs/{job_id}/original.pdf
    DATA_DIR/jobs/{job_id}/pages/page_{n}.png
    DATA_DIR/jobs/{job_id}/card_images/{card_id}/{question,answer}.png
"""

from pathlib import Path

from app.config import Settings


def job_dir(settings: Settings, job_id: int) -> Path:
    return settings.data_dir / "jobs" / str(job_id)


def original_pdf_path(settings: Settings, job_id: int) -> Path:
    return job_dir(settings, job_id) / "original.pdf"


def pages_dir(settings: Settings, job_id: int) -> Path:
    return job_dir(settings, job_id) / "pages"


def page_image_path(settings: Settings, job_id: int, page_number: int) -> Path:
    """Path for a rendered page image. `page_number` is 1-indexed.

    Rendering itself happens in M3; this only defines where the pipeline should
    write images and where the API should look for them.
    """
    return pages_dir(settings, job_id) / f"page_{page_number}.png"


def ensure_job_dir(settings: Settings, job_id: int) -> Path:
    """Create (if needed) and return the per-job directory."""
    directory = job_dir(settings, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def card_images_dir(settings: Settings, job_id: int, card_id: int) -> Path:
    """Directory holding a diagram card's composed question/answer images."""
    return job_dir(settings, job_id) / "card_images" / str(card_id)


def card_image_path(settings: Settings, job_id: int, card_id: int, side: str) -> Path:
    """Path for a composed diagram-card image. `side` is 'question' or 'answer'."""
    return card_images_dir(settings, job_id, card_id) / f"{side}.png"


def exports_dir(settings: Settings, job_id: int) -> Path:
    return job_dir(settings, job_id) / "exports"


def export_apkg_path(settings: Settings, job_id: int) -> Path:
    """Path for the built `.apkg` file. Re-exporting overwrites this file."""
    return exports_dir(settings, job_id) / "deck.apkg"
