"""Occlusion detection, grouping, and composition for the job pipeline (plan §5-6).

Everything here is diagram/text-occlusion-specific domain logic split out of
`pipeline.py`: turning raw detections into `Occlusion`s, applying the job's
grouping settings, and persisting + composing the resulting `CardDraft`s.
`pipeline.py` owns rendering, text-card generation, and job orchestration, and
calls into this module for the occlusion side of a run.
"""

from pathlib import Path
from typing import NamedTuple

from sqlmodel import Session

from app.config import Settings
from app.models import (
    Box,
    CardDraft,
    Direction,
    JobStage,
    NoteType,
    Occlusion,
    OcclusionKind,
)
from app.schemas.generation_options import GenerationOptions, MaskGrouping
from app.services.diagram_detection import DetectionPage, DiagramDetection, get_diagram_detector
from app.services.diagram_detection.compose import compose_occlusion
from app.services.diagram_detection.cropping import FULL_PAGE, derive_crop
from app.services.diagram_detection.ocr import OcrEngine
from app.services.job_progress import JobProgress, enter_stage
from app.services.occlusion_grouping import group_occlusions
from app.services.text_occlusion import TextPage, accept_spans, get_text_span_selector
from app.storage import card_image_path

_BOX_EPSILON = 1e-3

#: Front text for a grouped occlusion card, which asks for every answer on the
#: page at once. Individual cards keep their own per-mask front text.
_GROUPED_FRONT: dict[NoteType, str] = {
    NoteType.DIAGRAM: "Name all labeled parts",
    NoteType.TEXT_OCCLUSION: "Fill in the blanks",
}


def _contains(outer: Box, inner: Box) -> bool:
    """True if `inner` lies within `outer` (page-normalized), within a small epsilon."""
    return (
        inner.left >= outer.left - _BOX_EPSILON
        and inner.top >= outer.top - _BOX_EPSILON
        and inner.left + inner.width <= outer.left + outer.width + _BOX_EPSILON
        and inner.top + inner.height <= outer.top + outer.height + _BOX_EPSILON
    )


def occlusion_is_valid(occ: Occlusion) -> bool:
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


def build_identify_occlusions(detection: DiagramDetection, page_image: Path) -> list[Occlusion]:
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
        if occlusion_is_valid(occ):
            occlusions.append(occ)
    return occlusions


class PendingOcclusion(NamedTuple):
    """One detected occlusion awaiting card creation and image composition.

    Shared by both occlusion kinds, so `compose_occlusion_cards` needs no
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
) -> list[PendingOcclusion]:
    """Pass 1: detect diagram labels on each flagged page.

    Non-fatal: a page whose detection fails is skipped, never failing the job.
    """
    detector = get_diagram_detector(settings, ocr=ocr)
    pending: list[PendingOcclusion] = []
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
            PendingOcclusion(page_number, page_image, occ, NoteType.DIAGRAM, "What is this?")
            for occ in build_identify_occlusions(detection, page_image)
        )
        progress.advance()
    return pending


def select_text_occlusions(
    settings: Settings,
    image_dir: Path,
    page_count: int,
    progress: JobProgress,
    ocr: OcrEngine,
) -> tuple[list[PendingOcclusion], list[int]]:
    """Run text-span selection over every page, returning the pending text
    occlusions plus the pages the selector flagged as labeled diagrams (§5.4).

    Non-fatal per page: OCR or selection failing drops that page's text cards
    only. Everything after selection is deterministic, so failures there are
    bugs and are not swallowed.
    """
    selector = get_text_span_selector(settings)
    pending: list[PendingOcclusion] = []
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
                PendingOcclusion(
                    page_number,
                    page_image,
                    occ,
                    NoteType.TEXT_OCCLUSION,
                    "Fill in the blank",
                )
            )
        progress.advance()

    return pending, diagram_pages


def compose_occlusion_cards(
    session: Session,
    settings: Settings,
    job_id: int,
    pending: list[PendingOcclusion],
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


def group_pending(
    pending: list[PendingOcclusion], options: GenerationOptions
) -> list[PendingOcclusion]:
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

    buckets: dict[tuple[int, NoteType], list[PendingOcclusion]] = {}
    for item in pending:
        buckets.setdefault((item.page_number, item.note_type), []).append(item)

    grouped: list[PendingOcclusion] = []
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
            PendingOcclusion(page_number, first.page_image, occ, note_type, front)
            for occ in group_occlusions([member.occlusion for member in members], mode)
        )
    return grouped


def detect_diagram_occlusions(
    settings: Settings,
    options: GenerationOptions,
    image_dir: Path,
    diagram_pages: list[int],
    page_count: int,
    progress: JobProgress,
    ocr: OcrEngine,
) -> list[PendingOcclusion]:
    """Run diagram detection on the flagged pages, returning pending occlusions.

    Non-fatal: a page whose detection fails is skipped, never failing the job.
    Runs only on pages the cheap first-tier gate flagged, and only when the job's
    options enable diagram occlusion at all.
    """
    if not options.diagram_occlusion_enabled or not diagram_pages:
        return []

    enter_stage(progress, JobStage.DETECTING_MASKS, len(diagram_pages))
    return _detect_occlusions(settings, image_dir, diagram_pages, page_count, progress, ocr)
