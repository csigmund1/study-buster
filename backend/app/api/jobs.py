import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import CardDraft, Job, JobStatus
from app.schemas import CardDraftRead, JobRead
from app.schemas.generation_options import GenerationOptions, resolve_options
from app.services.anki_export import build_apkg, slugify
from app.services.job_progress import compute_progress, stage_label
from app.services.pipeline import run_job_pipeline
from app.storage import (
    ensure_job_dir,
    export_apkg_path,
    get_session,
    original_pdf_path,
    page_image_path,
)

router = APIRouter(tags=["jobs"])

PDF_MAGIC = b"%PDF-"


def _card_count(session: Session, job_id: int) -> int:
    rows = session.exec(
        select(CardDraft).where(CardDraft.job_id == job_id, CardDraft.is_deleted == False)  # noqa: E712
    ).all()
    return len(rows)


def _parse_options(raw: str | None, settings: Settings) -> GenerationOptions:
    """Parse the optional `options` form field into resolved options.

    Anything malformed — unparseable JSON, a non-object, an unknown key, or a
    value outside the documented enums — is a `422`. Called before any row or
    file is written so a rejected request leaves nothing behind.
    """
    if raw is None or not raw.strip():
        return resolve_options(None, settings)

    try:
        parsed: Any = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="options must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="options must be a JSON object.")

    try:
        return resolve_options(parsed, settings)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid generation options: {exc.errors()[0]['msg']}"
        ) from exc


def _to_job_read(session: Session, job: Job, settings: Settings) -> JobRead:
    assert job.id is not None
    progress_percent, eta_seconds = compute_progress(job)
    return JobRead(
        id=job.id,
        deck_name=job.deck_name,
        status=job.status,
        error_message=job.error_message,
        page_count=job.page_count,
        card_count=_card_count(session, job.id),
        stage=job.stage,
        stage_label=stage_label(job.stage),
        progress_percent=progress_percent,
        eta_seconds=eta_seconds,
        options=resolve_options(job.options, settings),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/jobs", response_model=JobRead, status_code=201)
async def create_job(
    background_tasks: BackgroundTasks,
    deck_name: str = Form(...),
    file: UploadFile = File(...),
    options: str | None = Form(default=None),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobRead:
    resolved_options = _parse_options(options, settings)

    deck_name = deck_name.strip()
    if not (1 <= len(deck_name) <= 100):
        raise HTTPException(status_code=422, detail="deck_name must be 1-100 characters.")

    content_type_ok = file.content_type in ("application/pdf", "application/x-pdf")
    filename_ok = (file.filename or "").lower().endswith(".pdf")
    if not (content_type_ok or filename_ok):
        raise HTTPException(status_code=422, detail="Uploaded file must be a PDF.")

    contents = await file.read()
    if len(contents) > settings.max_pdf_bytes:
        max_mb = settings.max_pdf_bytes // (1024 * 1024)
        raise HTTPException(status_code=422, detail=f"PDF exceeds the {max_mb} MB size limit.")
    if not contents.startswith(PDF_MAGIC):
        raise HTTPException(status_code=422, detail="File is not a valid PDF.")

    job = Job(
        deck_name=deck_name,
        pdf_path="",
        status=JobStatus.PENDING,
        options=resolved_options.model_dump(mode="json"),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.id is not None

    ensure_job_dir(settings, job.id)
    pdf_path = original_pdf_path(settings, job.id)
    pdf_path.write_bytes(contents)
    job.pdf_path = str(pdf_path)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(run_job_pipeline, job.id)

    return _to_job_read(session, job, settings)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(
    job_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobRead:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _to_job_read(session, job, settings)


@router.get("/jobs/{job_id}/cards", response_model=list[CardDraftRead])
def list_job_cards(job_id: int, session: Session = Depends(get_session)) -> list[CardDraftRead]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    cards = session.exec(
        select(CardDraft)
        .where(CardDraft.job_id == job_id, CardDraft.is_deleted == False)  # noqa: E712
        .order_by(col(CardDraft.source_page), col(CardDraft.id))
    ).all()
    return [CardDraftRead.model_validate(card, from_attributes=True) for card in cards]


@router.get("/jobs/{job_id}/pages/{page_number}")
def get_job_page(
    job_id: int,
    page_number: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.page_count is not None and not (1 <= page_number <= job.page_count):
        raise HTTPException(status_code=404, detail="Page out of range.")

    image_path = page_image_path(settings, job_id, page_number)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Page image not found.")

    return FileResponse(image_path)


@router.post("/jobs/{job_id}/export")
def export_job(
    job_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != JobStatus.READY:
        raise HTTPException(status_code=409, detail="Job is not ready for export.")

    cards = session.exec(
        select(CardDraft).where(CardDraft.job_id == job_id, CardDraft.is_deleted == False)  # noqa: E712
    ).all()

    output_path = export_apkg_path(settings, job_id)
    build_apkg(settings, job, list(cards), output_path)

    filename = f"{slugify(job.deck_name)}.apkg"
    return FileResponse(
        output_path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
