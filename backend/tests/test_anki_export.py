import json
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient

from app.services.anki_export.naming import deck_id_for, slugify
from tests.conftest import _upload


def test_export_happy_path_returns_valid_apkg_with_media(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    upload_response = _upload(client, "Biology Lecture 3!", minimal_pdf_bytes)
    job_id = upload_response.json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "ready"

    response = client.post(f"/jobs/{job_id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    content_disposition = response.headers["content-disposition"]
    assert content_disposition == 'attachment; filename="biology-lecture-3.apkg"'

    with zipfile.ZipFile(BytesIO(response.content)) as apkg:
        names = apkg.namelist()
        assert "collection.anki2" in names or "collection.anki21" in names
        assert "media" in names

        media_map = json.loads(apkg.read("media"))
        media_filenames = set(media_map.values())
        # The mock pipeline always flags exactly one card with needs_page_image=True,
        # referencing page 1 of this single-page PDF.
        assert f"sb-job{job_id}-page1.png" in media_filenames


def test_export_returns_409_when_job_not_ready(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    upload_response = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = upload_response.json()["id"]

    # Force the job back to a non-ready state; the mock pipeline already ran it to
    # "ready" synchronously via BackgroundTasks, so directly hit the DB via the API
    # is not available — instead exercise a job that never finished. We simulate
    # this by exporting a job whose id does not exist as "ready" using a fresh job
    # right after creation is not reliable since TestClient runs the background task
    # synchronously. Instead, patch the job status directly through the app's DB.
    from app.config import get_settings
    from app.models import Job, JobStatus
    from app.storage import session_for

    settings = get_settings()
    session = session_for(settings)
    job = session.get(Job, job_id)
    assert job is not None
    job.status = JobStatus.PROCESSING
    session.add(job)
    session.commit()
    session.close()

    response = client.post(f"/jobs/{job_id}/export")
    assert response.status_code == 409


def test_export_returns_404_for_unknown_job(client: TestClient) -> None:
    response = client.post("/jobs/9999/export")
    assert response.status_code == 404


def test_deck_id_is_deterministic_per_deck_name() -> None:
    assert deck_id_for("Biology Lecture 3") == deck_id_for("Biology Lecture 3")
    assert deck_id_for("Biology Lecture 3") != deck_id_for("Chemistry Lecture 1")


def test_slugify_produces_lowercase_alnum_hyphen_filename() -> None:
    assert slugify("Biology Lecture 3!") == "biology-lecture-3"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert slugify("") == "deck"
    assert slugify("!!!") == "deck"
