"""Build a `.apkg` file from a Job and its CardDrafts (plan.md §M5).

Text fields are HTML-escaped before being embedded in note fields, since card
content is model/user-generated text, not trusted HTML. Cards flagged
`needs_page_image` get their rendered source-page PNG attached as media, named
with a collision-proof, job-scoped filename (Anki's media namespace is flat
across an entire collection).
"""

import html
import shutil
import tempfile
from pathlib import Path

import genanki

from app.config import Settings
from app.models import CardDraft, Job, NoteType, Occlusion
from app.services.anki_export.models import BASIC_MODEL, CLOZE_MODEL, IMAGE_OCCLUSION_MODEL
from app.services.anki_export.naming import deck_id_for
from app.storage.paths import card_image_path, page_image_path


def _media_filename(job_id: int, page_number: int, image_path: Path) -> str:
    return f"sb-job{job_id}-page{page_number}{image_path.suffix}"


def _diagram_media_filename(job_id: int, card_id: int, image_path: Path) -> str:
    return f"sb-job{job_id}-card{card_id}{image_path.suffix}"


def _diagram_image_media(
    settings: Settings, job_id: int, card_id: int, media_dir: Path
) -> tuple[str, Path] | None:
    """Copy a diagram card's composed answer image into `media_dir`.

    Returns `(media_filename, media_path)`, or `None` if the composed answer
    image has not been produced yet (e.g. composition has not run).
    """
    image_path = card_image_path(settings, job_id, card_id, "answer")
    if not image_path.is_file():
        return None

    filename = _diagram_media_filename(job_id, card_id, image_path)
    media_path = media_dir / filename
    shutil.copyfile(image_path, media_path)
    return filename, media_path


def _occlusions_field(occlusion: Occlusion) -> str:
    """Build a single-cloze `Occlusions` field over the target's `label_box` —
    the label mask itself (there is no separate structure-level box).

    Coordinates are converted from page-normalized to crop-normalized (the
    Image field holds the cropped card image, not the full page).
    """
    crop_box = occlusion.crop_box
    label_box = occlusion.label_box
    cl = (label_box.left - crop_box.left) / crop_box.width
    ct = (label_box.top - crop_box.top) / crop_box.height
    cw = label_box.width / crop_box.width
    ch = label_box.height / crop_box.height
    shape = f"image-occlusion:rect:left={cl:.4f}:top={ct:.4f}:width={cw:.4f}:height={ch:.4f}:oi=1"
    return f"{{{{c1::{shape}}}}}"


def _page_image_media(
    settings: Settings, job_id: int, source_page: int | None, media_dir: Path
) -> tuple[str, Path] | None:
    """Copy the rendered page image into `media_dir` under a collision-proof name.

    Returns `(media_filename, media_path)`, or `None` if there is no rendered
    image for the page (e.g. rendering has not happened yet).
    """
    if source_page is None:
        return None
    image_path = page_image_path(settings, job_id, source_page)
    if not image_path.is_file():
        return None

    filename = _media_filename(job_id, source_page, image_path)
    media_path = media_dir / filename
    shutil.copyfile(image_path, media_path)
    return filename, media_path


def build_apkg(
    settings: Settings,
    job: Job,
    cards: list[CardDraft],
    output_path: Path,
) -> None:
    """Build a `.apkg` for `job`'s non-deleted `cards` and write it to `output_path`."""
    assert job.id is not None

    deck = genanki.Deck(deck_id_for(job.deck_name), job.deck_name)
    deck.add_model(BASIC_MODEL)
    deck.add_model(CLOZE_MODEL)
    deck.add_model(IMAGE_OCCLUSION_MODEL)

    media_files: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sb-apkg-media-") as tmp_dir_str:
        media_dir = Path(tmp_dir_str)

        for card in cards:
            if card.is_deleted:
                continue

            if card.note_type == NoteType.DIAGRAM:
                if card.id is None:
                    continue
                diagram_media = _diagram_image_media(settings, job.id, card.id, media_dir)
                if diagram_media is None:
                    continue
                filename, media_path = diagram_media
                occlusion = Occlusion.model_validate(card.occlusion)
                occlusions_field = _occlusions_field(occlusion)
                image_field = f'<img src="{html.escape(filename)}">'
                header = html.escape(card.front or "")
                back_extra = html.escape(card.back or "")
                media_files.append(str(media_path))
                note = genanki.Note(
                    model=IMAGE_OCCLUSION_MODEL,
                    fields=[occlusions_field, image_field, header, back_extra, ""],
                )
                deck.add_note(note)
                continue

            image_media: tuple[str, Path] | None = None
            if card.needs_page_image:
                image_media = _page_image_media(settings, job.id, card.source_page, media_dir)

            if card.note_type == NoteType.BASIC:
                front = html.escape(card.front or "")
                back = html.escape(card.back or "")
                if image_media is not None:
                    filename, media_path = image_media
                    back = f'{back}<br><img src="{html.escape(filename)}">'
                    media_files.append(str(media_path))
                note = genanki.Note(model=BASIC_MODEL, fields=[front, back])
            else:
                cloze_text = html.escape(card.cloze_text or "")
                extra = ""
                if image_media is not None:
                    filename, media_path = image_media
                    extra = f'<img src="{html.escape(filename)}">'
                    media_files.append(str(media_path))
                note = genanki.Note(model=CLOZE_MODEL, fields=[cloze_text, extra])

            deck.add_note(note)

        package = genanki.Package(deck, media_files=media_files)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        package.write_to_file(str(output_path))
