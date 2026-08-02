from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import CardDraft, Job, JobStatus, NoteType
from app.storage import session_for


def _create_job_and_card(
    note_type: NoteType = NoteType.BASIC,
    front: str | None = "What is 2+2?",
    back: str | None = "4",
    cloze_text: str | None = None,
) -> tuple[int, int]:
    """Insert a Job + CardDraft directly, bypassing the (stubbed) pipeline."""
    settings = get_settings()
    session = session_for(settings)
    try:
        job = Job(deck_name="Notes", pdf_path="", status=JobStatus.READY)
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.id is not None

        card = CardDraft(
            job_id=job.id,
            note_type=note_type,
            front=front,
            back=back,
            cloze_text=cloze_text,
        )
        session.add(card)
        session.commit()
        session.refresh(card)
        assert card.id is not None
        return job.id, card.id
    finally:
        session.close()


def test_update_basic_card_valid_edit(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card()

    response = client.put(
        f"/cards/{card_id}", json={"front": "Updated front", "back": "Updated back"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["front"] == "Updated front"
    assert body["back"] == "Updated back"
    assert body["note_type"] == "basic"


def test_update_card_rejects_invalid_cloze_syntax(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card(
        note_type=NoteType.CLOZE, front=None, back=None, cloze_text="{{c1::helicase}}"
    )

    response = client.put(f"/cards/{card_id}", json={"cloze_text": "no cloze markers here"})

    assert response.status_code == 422


def test_update_card_switch_note_type_basic_to_cloze(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card()

    response = client.put(
        f"/cards/{card_id}",
        json={"note_type": "cloze", "cloze_text": "{{c1::Helicase}} unwinds DNA."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["note_type"] == "cloze"
    assert body["cloze_text"] == "{{c1::Helicase}} unwinds DNA."
    # Server auto-clears fields irrelevant to the new note type.
    assert body["front"] is None
    assert body["back"] is None


def test_update_card_switch_note_type_to_basic_without_fields_fails(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card(
        note_type=NoteType.CLOZE, front=None, back=None, cloze_text="{{c1::helicase}}"
    )

    response = client.put(f"/cards/{card_id}", json={"note_type": "basic"})

    assert response.status_code == 422


def test_update_card_404_for_unknown_card(client: TestClient) -> None:
    response = client.put("/cards/9999", json={"front": "x", "back": "y"})
    assert response.status_code == 404


def test_delete_card_then_404_on_repeat(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card()

    first = client.delete(f"/cards/{card_id}")
    assert first.status_code == 204

    second = client.delete(f"/cards/{card_id}")
    assert second.status_code == 404


def test_deleted_card_excluded_from_job_cards_list(client: TestClient) -> None:
    job_id, card_id = _create_job_and_card()

    delete_response = client.delete(f"/cards/{card_id}")
    assert delete_response.status_code == 204

    cards_response = client.get(f"/jobs/{job_id}/cards")
    assert cards_response.status_code == 200
    assert cards_response.json() == []
