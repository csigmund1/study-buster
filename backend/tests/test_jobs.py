import httpx
import pytest
from fastapi.testclient import TestClient


def _upload(
    client: TestClient, deck_name: str, pdf_bytes: bytes, content_type: str = "application/pdf"
) -> httpx.Response:
    return client.post(
        "/jobs",
        data={"deck_name": deck_name},
        files={"file": ("lecture.pdf", pdf_bytes, content_type)},
    )


def test_upload_happy_path_reaches_ready(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    response = _upload(client, "Biology Lecture 3", minimal_pdf_bytes)

    assert response.status_code == 201
    body = response.json()
    assert body["deck_name"] == "Biology Lecture 3"
    assert body["card_count"] == 0
    # The response body reflects the Job as returned by the route (before the
    # background task runs), so it is still "pending" here.
    assert body["status"] == "pending"
    assert body["error_message"] is None

    # TestClient runs BackgroundTasks synchronously as part of the request/response
    # cycle, so by the time the client call returns, the stub pipeline has already
    # flipped the job to "ready".
    job_id = body["id"]
    get_response = client.get(f"/jobs/{job_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "ready"


def test_upload_rejects_empty_deck_name(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    response = _upload(client, "", minimal_pdf_bytes)
    assert response.status_code == 422


def test_upload_rejects_deck_name_over_100_chars(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    response = _upload(client, "x" * 101, minimal_pdf_bytes)
    assert response.status_code == 422


def test_upload_rejects_non_pdf_file(client: TestClient) -> None:
    response = client.post(
        "/jobs",
        data={"deck_name": "Notes"},
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 422


def test_upload_rejects_file_without_pdf_magic_bytes(client: TestClient) -> None:
    response = client.post(
        "/jobs",
        data={"deck_name": "Notes"},
        files={"file": ("lecture.pdf", b"this is not a real pdf body", "application/pdf")},
    )
    assert response.status_code == 422


def test_upload_rejects_file_over_size_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("MAX_PDF_BYTES", "10")
    response = _upload(client, "Notes", minimal_pdf_bytes)
    assert response.status_code == 422


def test_get_job_404_for_unknown_job(client: TestClient) -> None:
    response = client.get("/jobs/9999")
    assert response.status_code == 404


def test_list_cards_populated_after_mock_pipeline_runs(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    upload_response = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = upload_response.json()["id"]

    # The default CARD_GENERATOR is "mock", which is deterministic and always
    # produces cards for a non-empty page group (see test_pipeline.py for the
    # dedicated pipeline coverage).
    response = client.get(f"/jobs/{job_id}/cards")
    assert response.status_code == 200
    assert len(response.json()) > 0


def test_list_cards_404_for_unknown_job(client: TestClient) -> None:
    response = client.get("/jobs/9999/cards")
    assert response.status_code == 404


def test_get_page_404_when_job_unknown(client: TestClient) -> None:
    response = client.get("/jobs/9999/pages/1")
    assert response.status_code == 404


def test_get_page_404_when_page_out_of_range(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    upload_response = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = upload_response.json()["id"]

    response = client.get(f"/jobs/{job_id}/pages/999")
    assert response.status_code == 404


def test_get_page_200_returns_rendered_png_after_mock_pipeline_runs(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    upload_response = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = upload_response.json()["id"]

    response = client.get(f"/jobs/{job_id}/pages/1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
