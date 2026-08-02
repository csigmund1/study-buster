from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from app.models import CardDraft
from app.models.enums import NoteType
from app.schemas import CardDraftRead, CardDraftUpdate
from app.services.card_rules import CardValidationError, validate_card_fields
from app.storage import get_session

router = APIRouter(tags=["cards"])


@router.put("/cards/{card_id}", response_model=CardDraftRead)
def update_card(
    card_id: int, update: CardDraftUpdate, session: Session = Depends(get_session)
) -> CardDraftRead:
    card = session.get(CardDraft, card_id)
    if card is None or card.is_deleted:
        raise HTTPException(status_code=404, detail="Card not found.")

    changes = update.model_dump(exclude_unset=True)

    note_type = changes.get("note_type", card.note_type)
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
