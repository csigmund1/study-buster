from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import CardDraft
from app.models.enums import NoteType
from app.schemas import CardDraftRead, CardDraftUpdate
from app.services.card_rules import CardValidationError, validate_card_fields
from app.storage import card_image_path, get_session

router = APIRouter(tags=["cards"])


@router.put("/cards/{card_id}", response_model=CardDraftRead)
def update_card(
    card_id: int, update: CardDraftUpdate, session: Session = Depends(get_session)
) -> CardDraftRead:
    card = session.get(CardDraft, card_id)
    if card is None or card.is_deleted:
        raise HTTPException(status_code=404, detail="Card not found.")

    changes = update.model_dump(exclude_unset=True)
    requested_note_type = changes.get("note_type", card.note_type)

    # Diagram cards are fixed-type: their occlusion geometry is read-only and they
    # cannot switch note type (and nothing can switch *into* diagram). Only the
    # front/back text is editable.
    if NoteType.DIAGRAM in (card.note_type, requested_note_type):
        if card.note_type != NoteType.DIAGRAM or requested_note_type != NoteType.DIAGRAM:
            raise HTTPException(status_code=422, detail="Diagram cards cannot change note type.")
        front = changes.get("front", card.front)
        back = changes.get("back", card.back)
        try:
            validate_card_fields(NoteType.DIAGRAM, front, back, None)
        except CardValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        card.front = front
        card.back = back
        card.updated_at = datetime.now(UTC)
        session.add(card)
        session.commit()
        session.refresh(card)
        return CardDraftRead.model_validate(card, from_attributes=True)

    note_type = requested_note_type
    front = changes.get("front", card.front)
    back = changes.get("back", card.back)
    cloze_text = changes.get("cloze_text", card.cloze_text)

    try:
        validate_card_fields(note_type, front, back, cloze_text)
    except CardValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # The server owns field consistency: fields irrelevant to the note type are cleared.
    card.note_type = note_type
    if note_type == NoteType.BASIC:
        card.front = front
        card.back = back
        card.cloze_text = None
    else:
        card.front = None
        card.back = None
        card.cloze_text = cloze_text
    if "needs_page_image" in changes:
        card.needs_page_image = changes["needs_page_image"]
    card.updated_at = datetime.now(UTC)

    session.add(card)
    session.commit()
    session.refresh(card)
    return CardDraftRead.model_validate(card, from_attributes=True)


@router.get("/cards/{card_id}/image")
def get_card_image(
    card_id: int,
    side: str | None = Query(default=None),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    if side not in ("question", "answer"):
        raise HTTPException(status_code=400, detail="side must be 'question' or 'answer'.")

    card = session.get(CardDraft, card_id)
    if card is None or card.is_deleted:
        raise HTTPException(status_code=404, detail="Card not found.")
    if card.note_type != NoteType.DIAGRAM or card.id is None:
        raise HTTPException(status_code=404, detail="Card has no composed image.")

    image_path = card_image_path(settings, card.job_id, card.id, side)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Card image not found.")

    return FileResponse(image_path)


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int, session: Session = Depends(get_session)) -> Response:
    card = session.get(CardDraft, card_id)
    if card is None or card.is_deleted:
        raise HTTPException(status_code=404, detail="Card not found.")

    card.is_deleted = True
    card.updated_at = datetime.now(UTC)
    session.add(card)
    session.commit()
    return Response(status_code=204)
