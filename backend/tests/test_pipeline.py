import pytest
from fastapi.testclient import TestClient

from tests.conftest import _upload as _upload_response


def _upload(client: TestClient, deck_name: str, pdf_bytes: bytes) -> dict[str, object]:
    response = _upload_response(client, deck_name, pdf_bytes)
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
        assert card["note_type"] in ("basic", "cloze", "diagram")

    page_response = client.get(f"/jobs/{job_id}/pages/1")
    assert page_response.status_code == 200
    assert page_response.headers["content-type"] == "image/png"
    assert page_response.content.startswith(b"\x89PNG")

    # The mock generator flags page 1 as a diagram, and the mock detector yields
    # identify cards — so the diagram path runs end-to-end: occlusion payload
    # persisted and both composed images served.
    diagram_cards = [card for card in cards if card["note_type"] == "diagram"]
    assert diagram_cards, "expected the mock pipeline to produce diagram cards"
    diagram = diagram_cards[0]
    assert diagram["front"] and diagram["back"]
    assert diagram["occlusion"] is not None
    assert diagram["occlusion"]["direction"] == "identify"

    for side in ("question", "answer"):
        image_response = client.get(f"/cards/{diagram['id']}/image", params={"side": side})
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/png"
        assert image_response.content.startswith(b"\x89PNG")

    bad_side = client.get(f"/cards/{diagram['id']}/image", params={"side": "sideways"})
    assert bad_side.status_code == 400


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
