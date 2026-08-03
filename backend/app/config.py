"""Application configuration, sourced from environment variables with sensible defaults.

`get_settings()` re-reads the environment on every call (no caching) so that tests can set
DATA_DIR / DATABASE_URL / MAX_PDF_BYTES / MAX_PDF_PAGES via `monkeypatch.setenv(...)` before
the app starts up and get a fully isolated data directory and database per test.
"""

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 100
DEFAULT_CARD_GENERATOR = "mock"
DEFAULT_PAGE_GROUP_SIZE = 10
DEFAULT_RENDER_MAX_EDGE_PX = 1400
DEFAULT_DIAGRAM_DETECTOR = "mock"
DEFAULT_DIAGRAM_DETECTION_MODEL = "claude-haiku-4-5"
DEFAULT_DETECTION_MAX_EDGE_PX = 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    max_pdf_bytes: int
    max_pdf_pages: int
    card_generator: str
    page_group_size: int
    render_max_edge_px: int
    diagram_detector: str
    diagram_detection_model: str
    detection_max_edge_px: int


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)))
    database_url = os.environ.get("DATABASE_URL") or f"sqlite:///{data_dir / 'study_buster.db'}"
    max_pdf_bytes = int(os.environ.get("MAX_PDF_BYTES", str(DEFAULT_MAX_PDF_BYTES)))
    max_pdf_pages = int(os.environ.get("MAX_PDF_PAGES", str(DEFAULT_MAX_PDF_PAGES)))
    card_generator = os.environ.get("CARD_GENERATOR", DEFAULT_CARD_GENERATOR)
    page_group_size = int(os.environ.get("PAGE_GROUP_SIZE", str(DEFAULT_PAGE_GROUP_SIZE)))
    render_max_edge_px = int(
        os.environ.get("RENDER_MAX_EDGE_PX", str(DEFAULT_RENDER_MAX_EDGE_PX))
    )
    diagram_detector = os.environ.get("DIAGRAM_DETECTOR", DEFAULT_DIAGRAM_DETECTOR)
    diagram_detection_model = os.environ.get(
        "DIAGRAM_DETECTION_MODEL", DEFAULT_DIAGRAM_DETECTION_MODEL
    )
    detection_max_edge_px = int(
        os.environ.get("DETECTION_MAX_EDGE_PX", str(DEFAULT_DETECTION_MAX_EDGE_PX))
    )
    return Settings(
        data_dir=data_dir,
        database_url=database_url,
        max_pdf_bytes=max_pdf_bytes,
        max_pdf_pages=max_pdf_pages,
        card_generator=card_generator,
        page_group_size=page_group_size,
        render_max_edge_px=render_max_edge_px,
        diagram_detector=diagram_detector,
        diagram_detection_model=diagram_detection_model,
        detection_max_edge_px=detection_max_edge_px,
    )
