"""Native Image Occlusion export shape for diagram cards."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.config import get_settings
from app.models import Box, CardDraft, Direction, Job, JobStatus, NoteType, Occlusion
from app.services.anki_export import build_apkg
from app.services.anki_export.models import IMAGE_OCCLUSION_MODEL_ID
from app.storage.paths import card_image_path


def _occlusion() -> Occlusion:
    return Occlusion(
        direction=Direction.IDENTIFY,
        label="Thyroid gland",
        crop_box=Box(left=0.1, top=0.1, width=0.8, height=0.8),
        label_box=Box(left=0.3, top=0.3, width=0.2, height=0.1),
        mask_boxes=[Box(left=0.3, top=0.3, width=0.2, height=0.1)],
    )


def test_diagram_card_exports_one_image_occlusion_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings = get_settings()

    job = Job(id=1, deck_name="Anatomy", pdf_path="", status=JobStatus.READY)
    occ = _occlusion()
    card = CardDraft(
        id=1,
        job_id=1,
        note_type=NoteType.DIAGRAM,
        front="What is this?",
        back="Thyroid gland",
        occlusion=occ.model_dump(mode="json"),
        source_page=3,
    )

    # The builder reads the composed ANSWER image as the note's base image.
    answer_path = card_image_path(settings, 1, 1, "answer")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 90), "white").save(answer_path)

    out = tmp_path / "deck.apkg"
    build_apkg(settings, job, [card], out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        zf.extractall(tmp_path / "unz")
    media = json.loads((tmp_path / "unz" / "media").read_text())

    assert "collection.anki2" in names
    assert len(media) == 1  # exactly one media file bundled

    con = sqlite3.connect(tmp_path / "unz" / "collection.anki2")
    try:
        assert con.execute("select count(*) from notes").fetchone()[0] == 1
        assert con.execute("select count(*) from cards").fetchone()[0] == 1  # single card
        models = json.loads(con.execute("select models from col").fetchone()[0])
        io_model = models[str(IMAGE_OCCLUSION_MODEL_ID)]
        assert io_model["type"] == 1  # cloze-type notetype
        occlusions_field = con.execute("select flds from notes").fetchone()[0]
        assert "image-occlusion:rect" in occlusions_field
    finally:
        con.close()
