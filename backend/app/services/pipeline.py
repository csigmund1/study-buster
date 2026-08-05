"""Background job pipeline (plan.md §8): render pages, extract supplemental text,
generate + validate + dedup card drafts, and persist results.

Any exception raised while processing a job is caught and converted into a
`failed` Job with a readable `error_message` — never a raw traceback.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pymupdf
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models import (
    Box,
    CardDraft,
    Direction,
    Job,
    JobStage,
    JobStatus,
    NoteType,
    Occlusion,
    OcclusionKind,
)
from app.schemas.generation_options import (
    GenerationOptions,
    MaskGrouping,
    TextCardMode,
    resolve_options,
)
from app.services.card_generation import GroupPage, PageGroup, get_card_generator
from app.services.card_generation.schemas import GeneratedCard
from app.services.diagram_detection import DetectionPage, DiagramDetection, get_diagram_detector
from app.services.diagram_detection.compose import compose_occlusion
from app.services.diagram_detection.cropping import FULL_PAGE, derive_crop
from app.services.diagram_detection.ocr import AppleVisionOcr, OcrEngine
from app.services.document_processing import extract_page_texts, render_pages
from app.services.draft_validation import validate_and_dedupe
from app.services.job_progress import JobProgress
from app.services.occlusion_grouping import group_occlusions
from app.services.text_occlusion import TextPage, accept_spans, get_text_span_selector
from app.storage import card_image_path, pages_dir, session_for
from app.storage.paths import original_pdf_path

_BOX_EPSILON = 1e-3

#: Front text for a grouped occlusion card, which asks for every answer on the
#: page at once. Individual cards keep their own per-mask front text.
_GROUPED_FRONT: dict[NoteType, str] = {
    NoteType.DIAGRAM: "Name all labeled parts",
    NoteType.TEXT_OCCLUSION: "Fill in the blanks",
}


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


def _contains(outer: Box, inner: Box) -> bool:
    """True if `inner` lies within `outer` (page-normalized), within a small epsilon."""
    return (
        inner.left >= outer.left - _BOX_EPSILON
        and inner.top >= outer.top - _BOX_EPSILON
        and inner.left + inner.width <= outer.left + outer.width + _BOX_EPSILON
        and inner.top + inner.height <= outer.top + outer.height + _BOX_EPSILON
    )


def _occlusion_is_valid(occ: Occlusion) -> bool:
    """Geometry sanity: non-empty labels, and this card's own target boxes inside
    the crop.

    `Box` already guarantees coordinates in [0, 1] with positive size, so this only
    adds the non-empty-label check and containment of the target boxes essential to
    THIS card. `mask_boxes` (every label on the page, shared across the page's
    cards) is intentionally not required to be fully contained — a stray label box
    is clipped harmlessly at composition and must not invalidate the other cards.
    """
    if not occ.labels or not all(label.strip() for label in occ.labels):
        return False
    return all(_contains(occ.crop_box, target) for target in occ.target_boxes)


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
            kind=OcclusionKind.DIAGRAM,
            direction=Direction.IDENTIFY,
            labels=[label.text],
            crop_box=crop,
            target_boxes=[label.label_box],
            mask_boxes=label_boxes,
        )
        if _occlusion_is_valid(occ):
            occlusions.append(occ)
    return occlusions


def _enter_stage(progress: JobProgress, stage: JobStage, total: int) -> None:
    """Enter a stage only when it has real work: a zero denominator would mean an
    unknown percentage, so an empty stage is skipped entirely instead."""
    if total > 0:
        progress.stage(stage, total=total)


class _PendingOcclusion(NamedTuple):
    """One detected occlusion awaiting card creation and image composition.

    Shared by both occlusion kinds, so `_compose_occlusion_cards` needs no
    knowledge of which detector produced the item.
    """

    page_number: int
    page_image: Path
    occlusion: Occlusion
    note_type: NoteType
    front: str


def _answer_text(occlusion: Occlusion) -> str:
    """The card's back text: every answer this occlusion reveals, in order."""
    return ", ".join(occlusion.labels)


def _detect_occlusions(
    settings: Settings,
    image_dir: Path,
    diagram_pages: list[int],
    page_count: int,
    progress: JobProgress,
    ocr: OcrEngine,
) -> list[_PendingOcclusion]:
    """Pass 1: detect diagram labels on each flagged page.

    Non-fatal: a page whose detection fails is skipped, never failing the job.
    """
    detector = get_diagram_detector(settings, ocr=ocr)
    pending: list[_PendingOcclusion] = []
    for page_number in diagram_pages:
        if not (1 <= page_number <= page_count):
            progress.advance()
            continue
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            progress.advance()
            continue

        try:
            detection = detector.detect(
                DetectionPage(page_number=page_number, image_path=page_image)
            )
        except Exception:  # detection is best-effort; a failure drops this page only
            progress.advance()
            continue

        pending.extend(
            _PendingOcclusion(
                page_number, page_image, occ, NoteType.DIAGRAM, "What is this?"
            )
            for occ in _build_identify_occlusions(detection, page_image)
        )
        progress.advance()
    return pending


def _select_text_occlusions(
    settings: Settings,
    image_dir: Path,
    page_count: int,
    progress: JobProgress,
    ocr: OcrEngine,
) -> tuple[list[_PendingOcclusion], list[int]]:
    """Run text-span selection over every page, returning the pending text
    occlusions plus the pages the selector flagged as labeled diagrams (§5.4).

    Non-fatal per page: OCR or selection failing drops that page's text cards
    only. Everything after selection is deterministic, so failures there are
    bugs and are not swallowed.
    """
    selector = get_text_span_selector(settings)
    pending: list[_PendingOcclusion] = []
    diagram_pages: list[int] = []

    for page_number in range(1, page_count + 1):
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            progress.advance()
            continue

        try:
            lines = ocr.extract(page_image)
            selection = selector.select(
                TextPage(page_number=page_number, image_path=page_image, lines=lines)
            )
        except Exception:  # selection is best-effort; a failure drops this page only
            progress.advance()
            continue

        if selection.is_labeled_diagram:
            diagram_pages.append(page_number)

        for span in accept_spans(lines, selection.spans):
            occ = Occlusion(
                kind=OcclusionKind.TEXT,
                direction=Direction.IDENTIFY,
                labels=[span.answer],
                crop_box=FULL_PAGE,  # text cards are not cropped in v1
                target_boxes=span.boxes,
                mask_boxes=span.boxes,
            )
            pending.append(
                _PendingOcclusion(
                    page_number,
                    page_image,
                    occ,
                    NoteType.TEXT_OCCLUSION,
                    "Fill in the blank",
                )
            )
        progress.advance()

    return pending, diagram_pages


def _compose_occlusion_cards(
    session: Session,
    settings: Settings,
    job_id: int,
    pending: list[_PendingOcclusion],
    progress: JobProgress,
) -> None:
    """Pass 2: persist a `CardDraft` per detected occlusion and compose its images.

    Non-fatal: a card whose composition fails is dropped, never failing the job.
    """
    for item in pending:
        card = CardDraft(
            job_id=job_id,
            note_type=item.note_type,
            front=item.front,
            back=_answer_text(item.occlusion),
            occlusion=item.occlusion.model_dump(mode="json"),
            source_page=item.page_number,
        )
        session.add(card)
        session.flush()  # assign card.id for the composed-image paths
        assert card.id is not None

        question_out = card_image_path(settings, job_id, card.id, "question")
        answer_out = card_image_path(settings, job_id, card.id, "answer")
        try:
            compose_occlusion(item.page_image, item.occlusion, question_out, answer_out)
        except Exception:  # composition failed: drop the card, keep the job alive
            session.delete(card)
            session.flush()
        # Advance only after the add/delete decision: a progress commit must never
        # land between the flush and the delete.
        progress.advance()

    session.commit()


def _grouping_for(note_type: NoteType, options: GenerationOptions) -> MaskGrouping:
    """The grouping choice governing one occlusion kind.

    The two kinds are configured independently, so a job may group its text
    occlusions while leaving diagram masks as individual cards, or the reverse.
    """
    if note_type is NoteType.DIAGRAM:
        return options.diagram_mask_grouping
    if note_type is NoteType.TEXT_OCCLUSION:
        return options.text_mask_grouping
    return MaskGrouping.INDIVIDUAL  # non-occlusion note types never reach grouping


def _group_pending(
    pending: list[_PendingOcclusion], options: GenerationOptions
) -> list[_PendingOcclusion]:
    """Apply each kind's mask grouping across every pending occlusion.

    Buckets by `(page_number, note_type)` — `group_occlusions` merges one page's
    occlusions of one kind — preserving first-appearance order so card order stays
    deterministic. Each bucket is grouped according to ITS kind's setting. Runs
    before the `composing` stage is entered so its denominator matches the number
    of cards actually composed.
    """
    if (
        options.diagram_mask_grouping is MaskGrouping.INDIVIDUAL
        and options.text_mask_grouping is MaskGrouping.INDIVIDUAL
    ):
        return pending

    buckets: dict[tuple[int, NoteType], list[_PendingOcclusion]] = {}
    for item in pending:
        buckets.setdefault((item.page_number, item.note_type), []).append(item)

    grouped: list[_PendingOcclusion] = []
    for (page_number, note_type), members in buckets.items():
        mode = _grouping_for(note_type, options)
        first = members[0]
        # An individually-grouped bucket keeps its per-mask front text.
        front = (
            _GROUPED_FRONT.get(note_type, first.front)
            if mode is MaskGrouping.GROUPED
            else first.front
        )
        grouped.extend(
            _PendingOcclusion(page_number, first.page_image, occ, note_type, front)
            for occ in group_occlusions([member.occlusion for member in members], mode)
        )
    return grouped


def _detect_diagram_occlusions(
    settings: Settings,
    options: GenerationOptions,
    image_dir: Path,
    diagram_pages: list[int],
    page_count: int,
    progress: JobProgress,
    ocr: OcrEngine,
) -> list[_PendingOcclusion]:
    """Run diagram detection on the flagged pages, returning pending occlusions.

    Non-fatal: a page whose detection fails is skipped, never failing the job.
    Runs only on pages the cheap first-tier gate flagged, and only when the job's
    options enable diagram occlusion at all.
    """
    if not options.diagram_occlusion_enabled or not diagram_pages:
        return []

    _enter_stage(progress, JobStage.DETECTING_MASKS, len(diagram_pages))
    return _detect_occlusions(settings, image_dir, diagram_pages, page_count, progress, ocr)


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
    _enter_stage(progress, JobStage.RENDERING, declared_page_count)
    page_count = render_pages(
        pdf_path, image_dir, settings.render_max_edge_px, on_page=progress.advance
    )

    job.page_count = page_count
    job.updated_at = datetime.now(UTC)
    session.add(job)
    session.commit()

    _enter_stage(progress, JobStage.EXTRACTING, page_count)
    texts = extract_page_texts(pdf_path, on_page=progress.advance)

    # One OCR engine per job, shared by both features: it caches per page image,
    # so a page is recognized exactly once no matter who asks.
    ocr = AppleVisionOcr()

    # The two text stages are mutually exclusive; whichever runs also supplies the
    # diagram shortlist (plan §5.4). Diagram detection itself runs in both modes.
    text_pending: list[_PendingOcclusion] = []
    if options.text_card_mode is TextCardMode.TEXT_OCCLUSION:
        _enter_stage(progress, JobStage.GENERATING_CARDS, page_count)
        text_pending, diagram_pages = _select_text_occlusions(
            settings, image_dir, page_count, progress, ocr
        )
    else:
        groups = _build_page_groups(job.deck_name, image_dir, texts, settings.page_group_size)
        _enter_stage(progress, JobStage.GENERATING_CARDS, len(groups))
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

    diagram_pending = _detect_diagram_occlusions(
        settings, options, image_dir, diagram_pages, page_count, progress, ocr
    )

    # Both kinds compose in ONE stage. Entering `composing` twice would reset its
    # counter and make the reported percentage go backwards, which Phase 1
    # forbids. Grouping runs first so the stage total matches the cards composed.
    all_pending = _group_pending(diagram_pending + text_pending, options)
    if all_pending:
        _enter_stage(progress, JobStage.COMPOSING, len(all_pending))
        _compose_occlusion_cards(session, settings, job.id, all_pending, progress)


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

        _enter_stage(progress, JobStage.FINALIZING, 1)
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
