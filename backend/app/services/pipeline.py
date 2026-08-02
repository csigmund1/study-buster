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
from app.models import CardDraft, Job, JobStatus, NoteType
from app.services.card_generation import GroupPage, PageGroup, get_card_generator
from app.services.card_generation.schemas import GeneratedCard
from app.services.document_processing import extract_page_texts, render_pages
from app.services.draft_validation import validate_and_dedupe
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


def _generate_all_cards(settings: Settings, groups: list[PageGroup]) -> list[GeneratedCard]:
    generator = get_card_generator(settings)
    cards: list[GeneratedCard] = []
    for group in groups:
        cards.extend(generator.generate(group))
    return cards


def _enforce_page_limit(pdf_path: Path, max_pdf_pages: int) -> None:
    """Fail fast with a readable message if the PDF exceeds the page limit,
    before spending time rendering every page."""
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
    if page_count > max_pdf_pages:
        raise PipelineError(
            f"PDF has {page_count} pages, which exceeds the {max_pdf_pages}-page limit."
        )


def _process_job(session: Session, settings: Settings, job: Job) -> None:
    assert job.id is not None

    pdf_path = original_pdf_path(settings, job.id)
    _enforce_page_limit(pdf_path, settings.max_pdf_pages)

    image_dir = pages_dir(settings, job.id)
    page_count = render_pages(pdf_path, image_dir, settings.render_max_edge_px)

    job.page_count = page_count
    job.updated_at = datetime.now(UTC)
    session.add(job)
    session.commit()

    texts = extract_page_texts(pdf_path)
    groups = _build_page_groups(job.deck_name, image_dir, texts, settings.page_group_size)
    generated_cards = _generate_all_cards(settings, groups)
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


def run_job_pipeline(job_id: int) -> None:
    settings = get_settings()
    session = session_for(settings)
    try:
        job = session.get(Job, job_id)
        if job is None:
            return

        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()

        _process_job(session, settings, job)

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
