import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.models import Job
from app.storage import session_for
from tests.conftest import _upload

DEFAULT_OPTIONS = {
    "text_card_mode": "basic_cloze",
    "diagram_occlusion_enabled": True,
    "diagram_mask_grouping": "individual",
    "text_mask_grouping": "individual",
}


def _upload_with_options(
    client: TestClient, options: str, minimal_pdf_bytes: bytes
) -> httpx.Response:
    return client.post(
        "/jobs",
        data={"deck_name": "Options", "options": options},
        files={"file": ("lecture.pdf", minimal_pdf_bytes, "application/pdf")},
    )


def _stored_options(job_id: int) -> dict[str, object] | None:
    with session_for(get_settings()) as session:
        job = session.exec(select(Job).where(Job.id == job_id)).one()
        return job.options


def _job_row_count() -> int:
    with session_for(get_settings()) as session:
        return len(session.exec(select(Job)).all())


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


def test_upload_without_options_reports_the_documented_defaults(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    response = _upload(client, "Notes", minimal_pdf_bytes)
    assert response.status_code == 201
    job_id = response.json()["id"]

    assert response.json()["options"] == DEFAULT_OPTIONS
    assert client.get(f"/jobs/{job_id}").json()["options"] == DEFAULT_OPTIONS
    # The resolved options are stored, not the (absent) request payload.
    assert _stored_options(job_id) == DEFAULT_OPTIONS


def test_upload_options_round_trip(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    sent = {
        "text_card_mode": "text_occlusion",
        "diagram_occlusion_enabled": False,
        "diagram_mask_grouping": "individual",
        "text_mask_grouping": "grouped",
    }
    response = _upload_with_options(client, json.dumps(sent), minimal_pdf_bytes)

    assert response.status_code == 201
    job_id = response.json()["id"]
    assert response.json()["options"] == sent
    assert client.get(f"/jobs/{job_id}").json()["options"] == sent
    assert _stored_options(job_id) == sent


def test_upload_partial_options_are_resolved_server_side(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    response = _upload_with_options(
        client, json.dumps({"text_mask_grouping": "grouped"}), minimal_pdf_bytes
    )

    assert response.status_code == 201
    assert response.json()["options"] == {**DEFAULT_OPTIONS, "text_mask_grouping": "grouped"}


def test_upload_accepts_the_legacy_mask_grouping_key(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    """The pre-split key still applies to BOTH kinds, and is stored expanded."""
    response = _upload_with_options(
        client, json.dumps({"mask_grouping": "grouped"}), minimal_pdf_bytes
    )

    assert response.status_code == 201
    expanded = {
        **DEFAULT_OPTIONS,
        "diagram_mask_grouping": "grouped",
        "text_mask_grouping": "grouped",
    }
    assert response.json()["options"] == expanded
    assert _stored_options(response.json()["id"]) == expanded


def test_a_specific_grouping_key_beats_the_legacy_one(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    response = _upload_with_options(
        client,
        json.dumps({"mask_grouping": "grouped", "diagram_mask_grouping": "individual"}),
        minimal_pdf_bytes,
    )

    assert response.status_code == 201
    assert response.json()["options"] == {
        **DEFAULT_OPTIONS,
        "diagram_mask_grouping": "individual",
        "text_mask_grouping": "grouped",
    }


def test_upload_empty_options_object_matches_omitting_options(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    response = _upload_with_options(client, "{}", minimal_pdf_bytes)
    assert response.status_code == 201
    assert response.json()["options"] == DEFAULT_OPTIONS


@pytest.mark.parametrize(
    "options",
    [
        "{not json",
        "[]",
        '"basic_cloze"',
        json.dumps({"unknown_key": True}),
        json.dumps({"text_card_mode": "sideways"}),
        json.dumps({"text_mask_grouping": "clustered"}),
        json.dumps({"diagram_mask_grouping": "clustered"}),
        json.dumps({"mask_grouping": "clustered"}),
        json.dumps({"diagram_occlusion_enabled": "maybe"}),
    ],
)
def test_upload_rejects_malformed_options(
    client: TestClient, minimal_pdf_bytes: bytes, options: str
) -> None:
    before = _job_row_count()

    response = _upload_with_options(client, options, minimal_pdf_bytes)

    assert response.status_code == 422
    # A rejected request must leave no Job row behind.
    assert _job_row_count() == before
