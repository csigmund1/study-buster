from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.models import CardDraft, Job, JobStatus, NoteType
from app.storage import card_image_path, session_for
from tests.conftest import sample_occlusion


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


def _create_diagram_card(*, with_images: bool = True) -> tuple[int, int]:
    """Insert a Job + a diagram CardDraft with an occlusion payload, optionally
    writing composed question/answer images to their expected paths."""
    settings = get_settings()
    occ = sample_occlusion()
    session = session_for(settings)
    try:
        job = Job(deck_name="Anatomy", pdf_path="", status=JobStatus.READY)
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.id is not None

        card = CardDraft(
            job_id=job.id,
            note_type=NoteType.DIAGRAM,
            front="What is this?",
            back="Thyroid gland",
            occlusion=occ.model_dump(mode="json"),
            source_page=2,
        )
        session.add(card)
        session.commit()
        session.refresh(card)
        assert card.id is not None

        if with_images:
            for side in ("question", "answer"):
                path = card_image_path(settings, job.id, card.id, side)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (60, 40), "white").save(path)

        return job.id, card.id
    finally:
        session.close()


def test_diagram_card_edit_text_succeeds(client: TestClient) -> None:
    _job_id, card_id = _create_diagram_card()

    response = client.put(f"/cards/{card_id}", json={"front": "Name it", "back": "Thyroid"})

    assert response.status_code == 200
    body = response.json()
    assert body["front"] == "Name it"
    assert body["back"] == "Thyroid"
    assert body["note_type"] == "diagram"
    assert body["occlusion"] is not None


def test_diagram_card_cannot_switch_note_type(client: TestClient) -> None:
    _job_id, card_id = _create_diagram_card(with_images=False)

    response = client.put(
        f"/cards/{card_id}", json={"note_type": "basic", "front": "a", "back": "b"}
    )

    assert response.status_code == 422


def test_basic_card_cannot_switch_to_diagram(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card()

    response = client.put(f"/cards/{card_id}", json={"note_type": "diagram"})

    assert response.status_code == 422


def test_diagram_image_endpoint_serves_both_sides(client: TestClient) -> None:
    _job_id, card_id = _create_diagram_card()

    for side in ("question", "answer"):
        response = client.get(f"/cards/{card_id}/image", params={"side": side})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")


def test_diagram_image_endpoint_rejects_bad_side(client: TestClient) -> None:
    _job_id, card_id = _create_diagram_card()

    assert client.get(f"/cards/{card_id}/image", params={"side": "top"}).status_code == 400
    assert client.get(f"/cards/{card_id}/image").status_code == 400


def test_image_endpoint_404_for_non_diagram_card(client: TestClient) -> None:
    _job_id, card_id = _create_job_and_card()

    response = client.get(f"/cards/{card_id}/image", params={"side": "question"})
    assert response.status_code == 404
