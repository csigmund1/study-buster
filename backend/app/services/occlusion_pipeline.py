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
from app.services.diagram_detection.ocr import OcrEngine, OcrItem
from app.services.document_processing import extract_page_styles
from app.services.draft_validation import normalize_text
from app.services.job_progress import JobProgress, enter_stage
from app.services.occlusion_grouping import group_occlusions
from app.services.text_occlusion import (
    SelectedSpan,
    TextPage,
    accept_spans,
    detect_chrome_lines,
    detect_objectives,
    get_text_span_selector,
)
from app.services.text_occlusion.emphasis import annotate_emphasis
from app.services.text_occlusion.filters import MIN_LINE_CONFIDENCE
from app.storage import card_image_path

_BOX_EPSILON = 1e-3

#: A page with at least this many confident OCR lines, averaging at least this
#: many words each, reads as dense prose rather than a diagram: the diagram
#: classifier downstream is authoritative and comparatively cheap to run, so
#: this shortlist only exists to skip pages that are clearly all-text. It errs
#: toward inclusion — missing a real diagram is worse than one wasted
#: classifier call on a prose page that slips through.
DIAGRAM_PROSE_MIN_LINES = 20
DIAGRAM_PROSE_MIN_AVG_WORDS = 5.0

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


def dedupe_answers_across_pages(pending: list[PendingOcclusion]) -> list[PendingOcclusion]:
    """Drop occlusions whose answer an earlier one already asked for, job-wide.

    `filters.accept_spans` dedupes only within a page — it is handed one page's
    lines and cannot see the rest of the deck — but a lecture deck restates its
    own key terms, so the same phrase is genuinely selectable on several slides.
    One real-mode job carded `Frank-Starling mechanism` three times, from pages
    5, 8, and 10.

    Shaped after `draft_validation.validate_and_dedupe`, which does the same job
    for the basic/cloze path: one pass over the accumulated list, normalized
    keys, order preserved. Only the LATER occlusion is dropped, so the earliest
    page — and within a page the selector's own preference ordering — wins.

    Runs on ungrouped occlusions, one answer each, before `group_pending` merges
    a page's masks into a single card.
    """
    seen: set[str] = set()
    deduped: list[PendingOcclusion] = []
    for item in pending:
        key = normalize_text(_answer_text(item.occlusion))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(item)
    return deduped


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
    texts: list[str],
    pdf_path: Path,
) -> list[PendingOcclusion]:
    """Run batched, text-only span selection over every page, returning the
    pending text occlusions.

    Diagram detection is fully decoupled from this: it no longer reads a
    "labeled diagram" flag out of a text-occlusion response — see
    `shortlist_diagram_pages`.

    Non-fatal: a failed selection call drops spans for whatever pages it
    covered rather than failing the job — the batch is simply treated as
    empty. Everything after selection is deterministic, so failures there are
    bugs and are not swallowed.

    Answers are deduped across pages as well as within them: `accept_spans` sees
    one page at a time, so the job-wide pass is `dedupe_answers_across_pages`
    over the accumulated result.
    """
    # OCR every page up front, in page order, so `detect_chrome_lines` can see
    # the whole deck's furniture before any page is selected. The engine caches
    # per resolved image path, so a later `ocr.extract` call for the same page
    # (e.g. from diagram detection) is free — this is not a double OCR pass.
    pages_lines: list[list[OcrItem]] = []
    for page_number in range(1, page_count + 1):
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            pages_lines.append([])
            continue
        try:
            pages_lines.append(ocr.extract(page_image))
        except Exception:  # per-page OCR failure: this page contributes no lines
            pages_lines.append([])

    chrome_lines = detect_chrome_lines(pages_lines)

    # A learning-objectives / agenda slide is deck-level context, not a mask
    # source: its objectives steer which content spans are worth choosing, so it
    # is passed to the selector but never itself carded.
    objectives, objectives_pages = detect_objectives(texts)

    # The lecturer's bold/italic emphasis, recovered from the PDF font layer and
    # matched onto OCR lines so the selector can prioritize it. Best-effort: a
    # PDF whose styles cannot be read simply yields no emphasis, never a failed
    # job.
    try:
        page_styles = extract_page_styles(pdf_path)
    except Exception:
        page_styles = []

    pages = [
        TextPage(
            page_number=page_number,
            lines=annotate_emphasis(
                lines,
                page_styles[page_number - 1] if page_number - 1 < len(page_styles) else [],
            ),
        )
        for page_number, lines in enumerate(pages_lines, start=1)
        if lines and page_number not in objectives_pages
    ]

    selector = get_text_span_selector(settings)
    try:
        spans = selector.select(pages, chrome_lines, objectives=objectives)
    except Exception:  # selection is best-effort; a failure yields no spans at all
        spans = []

    spans_by_page: dict[int, list[SelectedSpan]] = {}
    for span in spans:
        if not span.refs:
            continue
        spans_by_page.setdefault(span.refs[0].page_number, []).append(span)

    pending: list[PendingOcclusion] = []
    for page_number in range(1, page_count + 1):
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            progress.advance()
            continue

        lines = pages_lines[page_number - 1]
        for accepted in accept_spans(lines, spans_by_page.get(page_number, []), chrome_lines):
            occ = Occlusion(
                kind=OcclusionKind.TEXT,
                direction=Direction.IDENTIFY,
                labels=[accepted.answer],
                crop_box=FULL_PAGE,  # text cards are not cropped in v1
                target_boxes=accepted.boxes,
                mask_boxes=accepted.boxes,
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

    return dedupe_answers_across_pages(pending)


def shortlist_diagram_pages(image_dir: Path, page_count: int, ocr: OcrEngine) -> list[int]:
    """The 1-indexed pages worth running the diagram classifier on.

    Free: OCR is cached per resolved image path by `ocr`, so this reuses
    whatever `select_text_occlusions` (or an earlier call to this function)
    already recognized. Conservative by design — the double-pass Haiku
    classifier downstream is authoritative on whether a page is really a
    labeled diagram, so this shortlist only needs to rule out pages that are
    unambiguously dense prose, never to positively identify a diagram. A page
    is skipped only when it has at least `DIAGRAM_PROSE_MIN_LINES` confident
    lines AND averages at least `DIAGRAM_PROSE_MIN_AVG_WORDS` words per
    confident line; a page with no OCR lines is not a candidate (there is
    nothing to run the classifier on).
    """
    candidates: list[int] = []
    for page_number in range(1, page_count + 1):
        page_image = image_dir / f"page_{page_number}.png"
        if not page_image.is_file():
            continue
        try:
            lines = ocr.extract(page_image)
        except Exception:  # OCR failure: not a candidate, nothing to classify from
            continue
        if not lines:
            continue

        confident = [line for line in lines if line.confidence >= MIN_LINE_CONFIDENCE]
        if not confident:
            candidates.append(page_number)
            continue

        avg_words = sum(len(line.text.split()) for line in confident) / len(confident)
        is_dense_prose = (
            len(confident) >= DIAGRAM_PROSE_MIN_LINES and avg_words >= DIAGRAM_PROSE_MIN_AVG_WORDS
        )
        if not is_dense_prose:
            candidates.append(page_number)
    return candidates


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
