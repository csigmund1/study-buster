from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import Box, Direction, Occlusion
from app.storage.database import _engine_cache


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient wired to an isolated DATA_DIR + sqlite DB for this test only."""
    data_dir = tmp_path / "data"
    db_path = tmp_path / "study_buster.db"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Import app.main lazily, after env vars are set, so any settings resolved at
    # import time (there are none, but this keeps the fixture defensive) see the
    # isolated environment. The app's dependencies re-read env on every call.
    from app.main import app

    _engine_cache.clear()
    with TestClient(app) as test_client:
        yield test_client
    _engine_cache.clear()


@pytest.fixture
def minimal_pdf_bytes() -> bytes:
    """A minimal, syntactically valid one-page PDF, hand-written (no PDF library)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources<<>>>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
        b"%%EOF\n"
    )


def _upload(
    client: TestClient, deck_name: str, pdf_bytes: bytes, content_type: str = "application/pdf"
) -> httpx.Response:
    return client.post(
        "/jobs",
        data={"deck_name": deck_name},
        files={"file": ("lecture.pdf", pdf_bytes, content_type)},
    )


def sample_occlusion() -> Occlusion:
    return Occlusion(
        direction=Direction.IDENTIFY,
        label="Thyroid gland",
        crop_box=Box(left=0.1, top=0.1, width=0.8, height=0.8),
        label_box=Box(left=0.3, top=0.3, width=0.2, height=0.1),
        mask_boxes=[Box(left=0.3, top=0.3, width=0.2, height=0.1)],
    )


def _box(left: float, top: float, width: float, height: float) -> Box:
    return Box(left=left, top=top, width=width, height=height)
