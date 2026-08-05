"""Native Image Occlusion export shape for diagram cards."""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.config import get_settings
from app.models import (
    Box,
    CardDraft,
    Direction,
    Job,
    JobStatus,
    NoteType,
    Occlusion,
    OcclusionKind,
)
from app.services.anki_export import build_apkg
from app.services.anki_export.models import IMAGE_OCCLUSION_MODEL_ID
from app.storage.paths import card_image_path
from tests.conftest import sample_occlusion


def test_diagram_card_exports_one_image_occlusion_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings = get_settings()

    job = Job(id=1, deck_name="Anatomy", pdf_path="", status=JobStatus.READY)
    occ = sample_occlusion()
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


def test_multi_target_occlusion_exports_one_card_with_every_shape_under_c1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grouped/multi-line occlusion must stay ONE Anki card.

    Every shape goes under `c1`; using `c1..cN` would silently produce N cards.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings = get_settings()

    job = Job(id=1, deck_name="Anatomy", pdf_path="", status=JobStatus.READY)
    occ = Occlusion(
        kind=OcclusionKind.TEXT,
        direction=Direction.IDENTIFY,
        labels=["Part of the trunk"],
        crop_box=Box(left=0.0, top=0.0, width=1.0, height=1.0),
        target_boxes=[
            Box(left=0.10, top=0.20, width=0.20, height=0.03),
            Box(left=0.10, top=0.24, width=0.15, height=0.03),
            Box(left=0.50, top=0.60, width=0.12, height=0.03),
        ],
        mask_boxes=[
            Box(left=0.10, top=0.20, width=0.20, height=0.03),
            Box(left=0.10, top=0.24, width=0.15, height=0.03),
            Box(left=0.50, top=0.60, width=0.12, height=0.03),
        ],
    )
    card = CardDraft(
        id=1,
        job_id=1,
        note_type=NoteType.TEXT_OCCLUSION,
        front="Fill in the blank",
        back="Part of the trunk",
        occlusion=occ.model_dump(mode="json"),
        source_page=3,
    )

    answer_path = card_image_path(settings, 1, 1, "answer")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 90), "white").save(answer_path)

    out = tmp_path / "deck.apkg"
    build_apkg(settings, job, [card], out)

    with zipfile.ZipFile(out) as zf:
        zf.extractall(tmp_path / "unz")

    con = sqlite3.connect(tmp_path / "unz" / "collection.anki2")
    try:
        assert con.execute("select count(*) from notes").fetchone()[0] == 1
        # Three shapes, still a single card — the c1-only invariant.
        assert con.execute("select count(*) from cards").fetchone()[0] == 1
        flds = con.execute("select flds from notes").fetchone()[0]
        assert flds.count("image-occlusion:rect") == 3
        assert flds.count("{{c1::") == 3
        assert "{{c2::" not in flds
    finally:
        con.close()
