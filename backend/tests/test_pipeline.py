import pytest
from fastapi.testclient import TestClient


def _upload(client: TestClient, deck_name: str, pdf_bytes: bytes) -> dict[str, object]:
    response = client.post(
        "/jobs",
        data={"deck_name": deck_name},
        files={"file": ("lecture.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()


def test_mock_pipeline_end_to_end(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    job = _upload(client, "Biology Lecture 3", minimal_pdf_bytes)
    job_id = job["id"]

    job_response = client.get(f"/jobs/{job_id}")
    assert job_response.status_code == 200
    job_body = job_response.json()
    assert job_body["status"] == "ready"
    assert job_body["page_count"] == 1
    assert job_body["card_count"] > 0

    cards_response = client.get(f"/jobs/{job_id}/cards")
    assert cards_response.status_code == 200
    cards = cards_response.json()
    assert len(cards) == job_body["card_count"]
    for card in cards:
        assert card["source_page"] == 1
        assert card["note_type"] in ("basic", "cloze")

    page_response = client.get(f"/jobs/{job_id}/pages/1")
    assert page_response.status_code == 200
    assert page_response.headers["content-type"] == "image/png"
    assert page_response.content.startswith(b"\x89PNG")


def test_pipeline_fails_job_when_pdf_exceeds_page_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("MAX_PDF_PAGES", "0")

    job = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = job["id"]

    job_response = client.get(f"/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "failed"
    assert body["error_message"] is not None
    assert "page" in body["error_message"].lower()
