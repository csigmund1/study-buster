import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import JobStage
from app.services import job_progress, pipeline
from app.services.diagram_detection.ocr import OcrItem
from tests.conftest import _upload as _upload_response
from tests.test_text_occlusion import SENTENCE, make_line


def _upload(client: TestClient, deck_name: str, pdf_bytes: bytes) -> dict[str, object]:
    response = _upload_response(client, deck_name, pdf_bytes)
    assert response.status_code == 201
    return response.json()


def test_mock_pipeline_end_to_end(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    job = _upload(client, "Biology Lecture 3", minimal_pdf_bytes)
    job_id = job["id"]

    job_response = client.get(f"/jobs/{job_id}")
    assert job_response.status_code == 200
    job_body = job_response.json()
    assert job_body["status"] == "ready"
    assert job_body["page_count"] == 1
    assert job_body["card_count"] > 0

    cards_response = client.get(f"/jobs/{job_id}/cards")
    assert cards_response.status_code == 200
    cards = cards_response.json()
    assert len(cards) == job_body["card_count"]
    for card in cards:
        assert card["source_page"] == 1
        assert card["note_type"] in ("basic", "cloze", "diagram")

    page_response = client.get(f"/jobs/{job_id}/pages/1")
    assert page_response.status_code == 200
    assert page_response.headers["content-type"] == "image/png"
    assert page_response.content.startswith(b"\x89PNG")

    # The mock generator flags page 1 as a diagram, and the mock detector yields
    # identify cards — so the diagram path runs end-to-end: occlusion payload
    # persisted and both composed images served.
    diagram_cards = [card for card in cards if card["note_type"] == "diagram"]
    assert diagram_cards, "expected the mock pipeline to produce diagram cards"
    diagram = diagram_cards[0]
    assert diagram["front"] and diagram["back"]
    assert diagram["occlusion"] is not None
    assert diagram["occlusion"]["direction"] == "identify"

    for side in ("question", "answer"):
        image_response = client.get(f"/cards/{diagram['id']}/image", params={"side": side})
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/png"
        assert image_response.content.startswith(b"\x89PNG")

    bad_side = client.get(f"/cards/{diagram['id']}/image", params={"side": "sideways"})
    assert bad_side.status_code == 400


def test_pipeline_fails_job_when_pdf_exceeds_page_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("MAX_PDF_PAGES", "0")

    job = _upload(client, "Notes", minimal_pdf_bytes)
    job_id = job["id"]

    job_response = client.get(f"/jobs/{job_id}")
    assert job_response.status_code == 200
    body = job_response.json()
    assert body["status"] == "failed"
    assert body["error_message"] is not None
    assert "page" in body["error_message"].lower()


def test_pipeline_advances_through_the_expected_stages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    seen: list[JobStage] = []
    original_stage = job_progress.JobProgress.stage

    def recording_stage(
        self: job_progress.JobProgress, name: JobStage, total: int | None = None
    ) -> None:
        seen.append(name)
        assert total is not None and total > 0, "a stage must never be entered with no work"
        original_stage(self, name, total)

    monkeypatch.setattr(job_progress.JobProgress, "stage", recording_stage)

    job = _upload(client, "Stages", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()
    assert body["status"] == "ready"

    # The mock generator flags page 1 as a diagram, so every stage runs.
    assert seen == [
        JobStage.RENDERING,
        JobStage.EXTRACTING,
        JobStage.GENERATING_CARDS,
        JobStage.DETECTING_MASKS,
        JobStage.COMPOSING,
        JobStage.FINALIZING,
    ]


def test_terminal_job_reports_full_progress(client: TestClient, minimal_pdf_bytes: bytes) -> None:
    job = _upload(client, "Progress", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()

    assert body["status"] == "ready"
    assert body["progress_percent"] == 100
    assert body["eta_seconds"] is None
    assert body["stage"] == JobStage.FINALIZING.value
    assert body["stage_label"] == "Finishing up"


def test_pending_job_response_has_no_progress_numbers(
    client: TestClient, minimal_pdf_bytes: bytes
) -> None:
    # The upload response is rendered before the background task runs.
    job = _upload(client, "Pending", minimal_pdf_bytes)
    assert job["status"] == "pending"
    assert job["stage"] is None
    assert job["stage_label"] is None
    assert job["progress_percent"] is None
    assert job["eta_seconds"] is None


def test_failing_progress_reporter_does_not_fail_the_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    class _ExplodingSession:
        def __getattr__(self, name: str) -> object:
            def _raise(*args: object, **kwargs: object) -> None:
                raise RuntimeError("progress backend is down")

            return _raise

    def exploding_init(self: job_progress.JobProgress, session: object, job_id: int) -> None:
        # Give the reporter a session that fails on every call, while leaving the
        # pipeline's own session intact.
        self._session = _ExplodingSession()  # type: ignore[assignment]
        self._job_id = job_id
        self._last_commit = 0.0

    monkeypatch.setattr(job_progress.JobProgress, "__init__", exploding_init)

    job = _upload(client, "Resilient", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()

    assert body["status"] == "ready"
    assert body["error_message"] is None
    assert body["card_count"] > 0


class _StubOcr:
    """A stand-in Vision engine: every page yields the same readable lines.

    The test PDF renders as a blank page, so real OCR would find no text at all;
    stubbing here keeps the mock pipeline deterministic and off the OCR engine.
    """

    def __init__(self) -> None:
        self.pages: list[Path] = []

    def extract(self, image_path: Path) -> list[OcrItem]:
        self.pages.append(image_path)
        return [
            make_line(SENTENCE, top=0.1),
            make_line("glomerular filtration produces the primary urine", top=0.3),
        ]


def test_text_occlusion_mode_produces_servable_cards(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    job = _upload(client, "Text Occlusion", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()
    assert body["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    text_cards = [card for card in cards if card["note_type"] == "text_occlusion"]
    assert text_cards, "expected text-occlusion cards in text_occlusion mode"

    card = text_cards[0]
    assert card["front"] == "Fill in the blank"
    assert card["back"]
    assert card["occlusion"]["kind"] == "text"
    assert card["occlusion"]["crop_box"] == {"left": 0, "top": 0, "width": 1, "height": 1}
    assert card["occlusion"]["target_boxes"] == card["occlusion"]["mask_boxes"]

    for side in ("question", "answer"):
        image_response = client.get(f"/cards/{card['id']}/image", params={"side": side})
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/png"
        assert image_response.content.startswith(b"\x89PNG")


def test_progress_percent_never_decreases_with_both_occlusion_kinds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    """Diagram and text cards must compose in ONE `composing` stage.

    Entering `composing` a second time resets its counter, dropping the reported
    percentage back to the stage's start boundary — Phase 1 forbids the
    percentage ever decreasing.
    """
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    seen: list[JobStage] = []
    original_stage = job_progress.JobProgress.stage

    def recording_stage(
        self: job_progress.JobProgress, name: JobStage, total: int | None = None
    ) -> None:
        seen.append(name)
        original_stage(self, name, total)

    monkeypatch.setattr(job_progress.JobProgress, "stage", recording_stage)

    job = _upload(client, "Both Kinds", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()
    assert body["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    note_types = {card["note_type"] for card in cards}
    assert "text_occlusion" in note_types
    assert "diagram" in note_types, "both kinds must be present for this to be meaningful"

    # The stage is entered at most once, so its counter never restarts.
    assert seen.count(JobStage.COMPOSING) == 1
    # And stages still only ever move forward.
    order = list(JobStage)
    assert [order.index(stage) for stage in seen] == sorted(order.index(s) for s in seen)


def test_text_occlusion_mode_does_not_call_the_card_generator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("the card generator must not run in text_occlusion mode")

    monkeypatch.setattr(pipeline, "get_card_generator", _explode)

    job = _upload(client, "No Generator", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()

    assert body["status"] == "ready"
    cards = client.get(f"/jobs/{job['id']}/cards").json()
    assert cards
    assert all(card["note_type"] != "basic" for card in cards)
    assert all(card["note_type"] != "cloze" for card in cards)


def test_basic_cloze_mode_is_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    calls: list[object] = []
    original = pipeline.get_card_generator

    def recording(settings: object) -> object:
        calls.append(settings)
        return original(settings)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "get_card_generator", recording)
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    job = _upload(client, "Default Mode", minimal_pdf_bytes)
    body = client.get(f"/jobs/{job['id']}").json()
    assert body["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    assert calls, "the default mode must still call the card generator"
    assert any(card["note_type"] == "basic" for card in cards)
    assert all(card["note_type"] != "text_occlusion" for card in cards)


def _upload_with_options(
    client: TestClient, deck_name: str, pdf_bytes: bytes, options: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        "/jobs",
        data={"deck_name": deck_name, "options": json.dumps(options)},
        files={"file": ("lecture.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def test_grouped_mode_emits_one_card_per_page_per_kind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    individual = _upload(client, "Individual", minimal_pdf_bytes)
    individual_cards = client.get(f"/jobs/{individual['id']}/cards").json()
    individual_diagrams = [c for c in individual_cards if c["note_type"] == "diagram"]
    assert len(individual_diagrams) > 1, "grouping is only meaningful with several masks"

    job = _upload_with_options(
        client,
        "Grouped",
        minimal_pdf_bytes,
        {
            "text_card_mode": "text_occlusion",
            "diagram_mask_grouping": "grouped",
            "text_mask_grouping": "grouped",
        },
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    by_type: dict[str, list[dict[str, object]]] = {}
    for card in cards:
        by_type.setdefault(card["note_type"], []).append(card)

    # One page in this PDF, so one card per kind.
    assert sorted(by_type) == ["diagram", "text_occlusion"]
    assert len(by_type["diagram"]) == 1
    assert len(by_type["text_occlusion"]) == 1

    diagram = by_type["diagram"][0]
    assert diagram["front"] == "Name all labeled parts"
    assert len(diagram["occlusion"]["labels"]) == len(individual_diagrams)
    assert len(diagram["occlusion"]["target_boxes"]) == len(individual_diagrams)
    # Every individual diagram card carried the same mask set; grouping dedupes it.
    assert diagram["occlusion"]["mask_boxes"] == individual_diagrams[0]["occlusion"]["mask_boxes"]
    assert diagram["back"] == ", ".join(diagram["occlusion"]["labels"])

    text_card = by_type["text_occlusion"][0]
    assert text_card["front"] == "Fill in the blanks"
    assert len(text_card["occlusion"]["labels"]) > 1

    for card in (diagram, text_card):
        for side in ("question", "answer"):
            image = client.get(f"/cards/{card['id']}/image", params={"side": side})
            assert image.status_code == 200
            assert image.content.startswith(b"\x89PNG")


def test_grouped_mode_composes_in_one_stage_with_a_matching_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    totals: list[tuple[JobStage, int | None]] = []
    original_stage = job_progress.JobProgress.stage

    def recording_stage(
        self: job_progress.JobProgress, name: JobStage, total: int | None = None
    ) -> None:
        totals.append((name, total))
        original_stage(self, name, total)

    monkeypatch.setattr(job_progress.JobProgress, "stage", recording_stage)

    job = _upload_with_options(
        client,
        "Grouped Stages",
        minimal_pdf_bytes,
        {
            "text_card_mode": "text_occlusion",
            "diagram_mask_grouping": "grouped",
            "text_mask_grouping": "grouped",
        },
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    composing = [total for stage, total in totals if stage is JobStage.COMPOSING]
    assert len(composing) == 1, "composing must be entered exactly once"
    # The denominator counts the grouped cards, not the pre-grouping masks.
    assert composing[0] == len(client.get(f"/jobs/{job['id']}/cards").json())


@pytest.mark.parametrize(
    ("diagram_mode", "text_mode"),
    [("individual", "grouped"), ("grouped", "individual")],
)
def test_each_kind_groups_independently(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    minimal_pdf_bytes: bytes,
    diagram_mode: str,
    text_mode: str,
) -> None:
    """One kind may be grouped while the other stays individual."""
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    baseline = _upload_with_options(
        client,
        "Baseline",
        minimal_pdf_bytes,
        {"text_card_mode": "text_occlusion"},
    )
    baseline_cards = client.get(f"/jobs/{baseline['id']}/cards").json()
    counts = {"diagram": 0, "text_occlusion": 0}
    for card in baseline_cards:
        counts[str(card["note_type"])] += 1
    assert counts["diagram"] > 1 and counts["text_occlusion"] > 1, (
        "both kinds need several masks for grouping to be observable"
    )

    job = _upload_with_options(
        client,
        "Mixed",
        minimal_pdf_bytes,
        {
            "text_card_mode": "text_occlusion",
            "diagram_mask_grouping": diagram_mode,
            "text_mask_grouping": text_mode,
        },
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    by_type: dict[str, list[dict[str, object]]] = {}
    for card in client.get(f"/jobs/{job['id']}/cards").json():
        by_type.setdefault(str(card["note_type"]), []).append(card)

    # This PDF is one page, so a grouped kind collapses to exactly one card
    # while the individual kind keeps its full per-mask count.
    expected = {
        "diagram": 1 if diagram_mode == "grouped" else counts["diagram"],
        "text_occlusion": 1 if text_mode == "grouped" else counts["text_occlusion"],
    }
    assert {kind: len(cards) for kind, cards in by_type.items()} == expected

    grouped_fronts = {"diagram": "Name all labeled parts", "text_occlusion": "Fill in the blanks"}
    individual_fronts = {"diagram": "What is this?", "text_occlusion": "Fill in the blank"}
    for kind, mode in (("diagram", diagram_mode), ("text_occlusion", text_mode)):
        wanted = grouped_fronts[kind] if mode == "grouped" else individual_fronts[kind]
        assert all(card["front"] == wanted for card in by_type[kind])
        if mode == "grouped":
            assert len(by_type[kind][0]["occlusion"]["labels"]) == counts[kind]  # type: ignore[index]
        else:
            assert all(len(card["occlusion"]["labels"]) == 1 for card in by_type[kind])  # type: ignore[index]


def test_disabling_diagram_occlusion_removes_diagram_cards(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    seen: list[JobStage] = []
    original_stage = job_progress.JobProgress.stage

    def recording_stage(
        self: job_progress.JobProgress, name: JobStage, total: int | None = None
    ) -> None:
        seen.append(name)
        original_stage(self, name, total)

    monkeypatch.setattr(job_progress.JobProgress, "stage", recording_stage)

    job = _upload_with_options(
        client, "No Diagrams", minimal_pdf_bytes, {"diagram_occlusion_enabled": False}
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    assert all(card["note_type"] != "diagram" for card in cards)
    assert any(card["note_type"] in ("basic", "cloze") for card in cards)
    # The mask-detection stage must not be entered at all.
    assert JobStage.DETECTING_MASKS not in seen


def test_disabling_diagram_occlusion_keeps_text_occlusion_cards(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    job = _upload_with_options(
        client,
        "Text Only",
        minimal_pdf_bytes,
        {"text_card_mode": "text_occlusion", "diagram_occlusion_enabled": False},
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    assert cards
    assert {card["note_type"] for card in cards} == {"text_occlusion"}


def test_explicit_default_options_match_the_env_default_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    """Individual basic/cloze behavior is unchanged when the options are explicit."""
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    # Same deck name: the mock generator's card text embeds it.
    baseline = _upload(client, "Same Deck", minimal_pdf_bytes)
    baseline_cards = client.get(f"/jobs/{baseline['id']}/cards").json()

    explicit = _upload_with_options(
        client,
        "Same Deck",
        minimal_pdf_bytes,
        {
            "text_card_mode": "basic_cloze",
            "diagram_occlusion_enabled": True,
            "diagram_mask_grouping": "individual",
            "text_mask_grouping": "individual",
        },
    )
    explicit_cards = client.get(f"/jobs/{explicit['id']}/cards").json()

    assert [card["note_type"] for card in baseline_cards] == [
        card["note_type"] for card in explicit_cards
    ]
    assert [card["front"] for card in baseline_cards] == [
        card["front"] for card in explicit_cards
    ]


def test_job_options_override_the_environment_text_card_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)

    job = _upload_with_options(
        client, "Env Override", minimal_pdf_bytes, {"text_card_mode": "basic_cloze"}
    )
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    cards = client.get(f"/jobs/{job['id']}/cards").json()
    assert any(card["note_type"] == "basic" for card in cards)
    assert all(card["note_type"] != "text_occlusion" for card in cards)


def test_text_occlusion_mode_never_enters_an_empty_stage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, minimal_pdf_bytes: bytes
) -> None:
    seen: list[JobStage] = []
    original_stage = job_progress.JobProgress.stage

    def recording_stage(
        self: job_progress.JobProgress, name: JobStage, total: int | None = None
    ) -> None:
        seen.append(name)
        assert total is not None and total > 0, "a stage must never be entered with no work"
        original_stage(self, name, total)

    monkeypatch.setenv("TEXT_CARD_MODE", "text_occlusion")
    monkeypatch.setattr(pipeline, "AppleVisionOcr", _StubOcr)
    monkeypatch.setattr(job_progress.JobProgress, "stage", recording_stage)

    job = _upload(client, "Stages Text", minimal_pdf_bytes)
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "ready"

    assert seen[:3] == [JobStage.RENDERING, JobStage.EXTRACTING, JobStage.GENERATING_CARDS]
    assert JobStage.COMPOSING in seen
    assert seen[-1] == JobStage.FINALIZING
