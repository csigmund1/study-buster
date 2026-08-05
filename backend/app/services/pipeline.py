"""Background job pipeline (plan.md §8): render pages, extract supplemental text,
generate + validate + dedup card drafts, and persist results.

Any exception raised while processing a job is caught and converted into a
`failed` Job with a readable `error_message` — never a raw traceback.
"""

from datetime import UTC, datetime
from pathlib import Path

import pymupdf
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import CardDraft, Job, JobStage, JobStatus, NoteType
from app.schemas.generation_options import GenerationOptions, TextCardMode, resolve_options
from app.services.card_generation import GroupPage, PageGroup, get_card_generator
from app.services.card_generation.schemas import GeneratedCard
from app.services.diagram_detection.ocr import AppleVisionOcr
from app.services.document_processing import extract_page_texts, render_pages
from app.services.draft_validation import validate_and_dedupe
from app.services.job_progress import JobProgress, enter_stage
from app.services.occlusion_pipeline import (
    PendingOcclusion,
    compose_occlusion_cards,
    detect_diagram_occlusions,
    group_pending,
    select_text_occlusions,
)
from app.storage import pages_dir, session_for
from app.storage.paths import original_pdf_path


class PipelineError(RuntimeError):
    """Raised for pipeline-specific failures with an already-readable message."""


def _build_page_groups(
    deck_name: str,
    image_dir: Path,
    texts: list[str],
    group_size: int,
) -> list[PageGroup]:
    page_count = len(texts)
    groups: list[PageGroup] = []
    for start in range(0, page_count, group_size):
        end = min(start + group_size, page_count)
        pages = [
            GroupPage(
                page_number=page_number,
                image_path=image_dir / f"page_{page_number}.png",
                supplemental_text=texts[page_number - 1],
            )
            for page_number in range(start + 1, end + 1)
        ]
        groups.append(PageGroup(deck_name=deck_name, pages=pages))
    return groups


def _generate_all_cards(
    settings: Settings, groups: list[PageGroup], progress: JobProgress
) -> tuple[list[GeneratedCard], list[int]]:
    """Return all generated text cards plus the sorted, de-duplicated list of page
    numbers flagged as containing a labeled diagram (the cheap detection gate)."""
    generator = get_card_generator(settings)
    cards: list[GeneratedCard] = []
    diagram_pages: set[int] = set()
    for group in groups:
        result = generator.generate(group)
        cards.extend(result.cards)
        diagram_pages.update(result.diagram_pages)
        progress.advance()
    return cards, sorted(diagram_pages)


def _enforce_page_limit(pdf_path: Path, max_pdf_pages: int) -> int:
    """Return the PDF's page count, failing fast with a readable message if it
    exceeds the page limit — before spending time rendering every page."""
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
    if page_count > max_pdf_pages:
        raise PipelineError(
            f"PDF has {page_count} pages, which exceeds the {max_pdf_pages}-page limit."
        )
    return page_count


def _process_job(
    session: Session,
    settings: Settings,
    options: GenerationOptions,
    job: Job,
    progress: JobProgress,
) -> None:
    assert job.id is not None

    pdf_path = original_pdf_path(settings, job.id)
    declared_page_count = _enforce_page_limit(pdf_path, settings.max_pdf_pages)

    image_dir = pages_dir(settings, job.id)
    enter_stage(progress, JobStage.RENDERING, declared_page_count)
    page_count = render_pages(
        pdf_path, image_dir, settings.render_max_edge_px, on_page=progress.advance
    )

    job.page_count = page_count
    job.updated_at = datetime.now(UTC)
    session.add(job)
    session.commit()

    enter_stage(progress, JobStage.EXTRACTING, page_count)
    texts = extract_page_texts(pdf_path, on_page=progress.advance)

    # One OCR engine per job, shared by both features: it caches per page image,
    # so a page is recognized exactly once no matter who asks.
    ocr = AppleVisionOcr()

    # The two text stages are mutually exclusive; whichever runs also supplies the
    # diagram shortlist (plan §5.4). Diagram detection itself runs in both modes.
    text_pending: list[PendingOcclusion] = []
    if options.text_card_mode is TextCardMode.TEXT_OCCLUSION:
        enter_stage(progress, JobStage.GENERATING_CARDS, page_count)
        text_pending, diagram_pages = select_text_occlusions(
            settings, image_dir, page_count, progress, ocr
        )
    else:
        groups = _build_page_groups(job.deck_name, image_dir, texts, settings.page_group_size)
        enter_stage(progress, JobStage.GENERATING_CARDS, len(groups))
        generated_cards, diagram_pages = _generate_all_cards(settings, groups, progress)
        accepted_cards = validate_and_dedupe(generated_cards, page_count)

        for card in accepted_cards:
            session.add(
                CardDraft(
                    job_id=job.id,
                    note_type=NoteType(card.note_type),
                    front=card.front,
                    back=card.back,
                    cloze_text=card.cloze_text,
                    source_page=card.source_page,
                    needs_page_image=card.needs_page_image,
                )
            )
        session.commit()

    diagram_pending = detect_diagram_occlusions(
        settings, options, image_dir, diagram_pages, page_count, progress, ocr
    )

    # Both kinds compose in ONE stage. Entering `composing` twice would reset its
    # counter and make the reported percentage go backwards, which Phase 1
    # forbids. Grouping runs first so the stage total matches the cards composed.
    all_pending = group_pending(diagram_pending + text_pending, options)
    if all_pending:
        enter_stage(progress, JobStage.COMPOSING, len(all_pending))
        compose_occlusion_cards(session, settings, job.id, all_pending, progress)


def run_job_pipeline(job_id: int) -> None:
    settings = get_settings()
    session = session_for(settings)
    try:
        job = session.get(Job, job_id)
        if job is None:
            return

        job.status = JobStatus.PROCESSING
        job.processing_started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()

        # The job's own options win; missing keys (including a pre-feature row's
        # NULL) resolve to the documented server-side defaults.
        options = resolve_options(job.options, settings)

        progress = JobProgress(session, job_id)
        _process_job(session, settings, options, job, progress)

        enter_stage(progress, JobStage.FINALIZING, 1)
        progress.advance()

        job.status = JobStatus.READY
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
    except Exception as exc:  # convert any pipeline failure into a Job failure
        session.rollback()
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            job.updated_at = datetime.now(UTC)
            session.add(job)
            session.commit()
    finally:
        session.close()
