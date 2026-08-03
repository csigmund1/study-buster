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
from app.models import Box, CardDraft, Direction, Job, JobStatus, NoteType, Occlusion
from app.services.card_generation import GroupPage, PageGroup, get_card_generator
from app.services.card_generation.schemas import GeneratedCard
from app.services.diagram_detection import DetectionPage, DiagramDetection, get_diagram_detector
from app.services.diagram_detection.compose import compose_identify
from app.services.diagram_detection.cropping import FULL_PAGE, derive_crop
from app.services.document_processing import extract_page_texts, render_pages
from app.services.draft_validation import validate_and_dedupe
from app.storage import card_image_path, pages_dir, session_for
from app.storage.paths import original_pdf_path

_BOX_EPSILON = 1e-3


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
    settings: Settings, groups: list[PageGroup]
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
    return cards, sorted(diagram_pages)


def _contains(outer: Box, inner: Box) -> bool:
    """True if `inner` lies within `outer` (page-normalized), within a small epsilon."""
    return (
        inner.left >= outer.left - _BOX_EPSILON
        and inner.top >= outer.top - _BOX_EPSILON
        and inner.left + inner.width <= outer.left + outer.width + _BOX_EPSILON
        and inner.top + inner.height <= outer.top + outer.height + _BOX_EPSILON
    )


def _occlusion_is_valid(occ: Occlusion) -> bool:
    """Geometry sanity: non-empty label, and this card's own label box inside
    the crop.

    `Box` already guarantees coordinates in [0, 1] with positive size, so this only
    adds the non-empty-label check and containment of the label box essential to
    THIS card. `mask_boxes` (every label on the page, shared across the page's
    cards) is intentionally not required to be fully contained — a stray label box
    is clipped harmlessly at composition and must not invalidate the other cards.
    """
    if not occ.label.strip():
        return False
    return _contains(occ.crop_box, occ.label_box)


def _build_identify_occlusions(
    detection: DiagramDetection, page_image: Path
) -> list[Occlusion]:
    """Map one page's `DiagramDetection` into per-label identify `Occlusion`s.

    Every label's text box masks the question side; the target label's own box is
    revealed on the answer side. The crop is derived deterministically from the
    label boxes (see `cropping.derive_crop`); invalid geometry is dropped silently.
    """
    if not detection.is_labeled_diagram or not detection.labels:
        return []

    label_boxes = [label.label_box for label in detection.labels]
    try:
        crop = derive_crop(page_image, label_boxes, detection.diagram_box)
    except Exception:  # crop derivation is best-effort; fall back to the full page
        crop = FULL_PAGE

    occlusions: list[Occlusion] = []
    for label in detection.labels:
        occ = Occlusion(
            direction=Direction.IDENTIFY,
            label=label.text,
            crop_box=crop,
            label_box=label.label_box,
            mask_boxes=label_boxes,
        )
        if _occlusion_is_valid(occ):
            occlusions.append(occ)
    return occlusions


def _create_diagram_cards(
    session: Session,
    settings: Settings,
    job: Job,
    image_dir: Path,
    diagram_pages: list[int],
    page_count: int,
) -> None:
    """Run diagram detection on the flagged pages and persist identify cards.

    Non-fatal throughout: a page whose detection or composition fails is skipped,
    never failing the job. Runs only on pages the cheap first-tier gate flagged.
    """
    assert job.id is not None
    if not diagram_pages:
        return

    detector = get_diagram_detector(settings)
    for page_number in diagram_pages:
        if not (1 <= page_number <= page_count):
            continue
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            continue

        try:
            detection = detector.detect(
                DetectionPage(page_number=page_number, image_path=page_image)
            )
        except Exception:  # detection is best-effort; a failure drops this page only
            continue

        for occ in _build_identify_occlusions(detection, page_image):
            card = CardDraft(
                job_id=job.id,
                note_type=NoteType.DIAGRAM,
                front="What is this?",
                back=occ.label,
                occlusion=occ.model_dump(mode="json"),
                source_page=page_number,
            )
            session.add(card)
            session.flush()  # assign card.id for the composed-image paths
            assert card.id is not None

            question_out = card_image_path(settings, job.id, card.id, "question")
            answer_out = card_image_path(settings, job.id, card.id, "answer")
            try:
                compose_identify(page_image, occ, question_out, answer_out)
            except Exception:  # composition failed: drop the card, keep the job alive
                session.delete(card)
                session.flush()

    session.commit()


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
    generated_cards, diagram_pages = _generate_all_cards(settings, groups)
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

    _create_diagram_cards(session, settings, job, image_dir, diagram_pages, page_count)


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
