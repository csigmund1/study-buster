from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Job, JobStage, JobStatus
from app.services.job_progress import (
    STAGE_WEIGHTS,
    JobProgress,
    compute_progress,
    stage_label,
    start_boundary,
)


def _job(
    *,
    status: JobStatus = JobStatus.PROCESSING,
    stage: JobStage | None = None,
    completed: int | None = 0,
    total: int | None = None,
    elapsed_seconds: float | None = None,
) -> Job:
    started = (
        datetime.now(UTC) - timedelta(seconds=elapsed_seconds)
        if elapsed_seconds is not None
        else None
    )
    job = Job(deck_name="Deck", pdf_path="", status=status)
    job.stage = stage
    job.stage_completed = completed  # type: ignore[assignment]  # legacy rows read back NULL
    job.stage_total = total
    job.processing_started_at = started
    return job


def test_weights_sum_to_one_hundred() -> None:
    assert sum(STAGE_WEIGHTS.values()) == 100.0


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (JobStage.RENDERING, 0.0),
        (JobStage.EXTRACTING, 10.0),
        (JobStage.GENERATING_CARDS, 15.0),
        (JobStage.DETECTING_MASKS, 60.0),
        (JobStage.COMPOSING, 90.0),
        (JobStage.FINALIZING, 95.0),
    ],
)
def test_start_boundaries(stage: JobStage, expected: float) -> None:
    assert start_boundary(stage) == expected


def test_stage_labels_match_contract() -> None:
    assert stage_label(None) is None
    assert stage_label(JobStage.RENDERING) == "Rendering pages"
    assert stage_label(JobStage.EXTRACTING) == "Extracting text"
    assert stage_label(JobStage.GENERATING_CARDS) == "Generating cards"
    assert stage_label(JobStage.DETECTING_MASKS) == "Finding diagram labels"
    assert stage_label(JobStage.COMPOSING) == "Composing card images"
    assert stage_label(JobStage.FINALIZING) == "Finishing up"
    for stage in JobStage:
        assert stage_label(stage) is not None


@pytest.mark.parametrize(
    ("stage", "completed", "total", "expected"),
    [
        (JobStage.RENDERING, 0, 4, 0.0),  # stage start boundary
        (JobStage.RENDERING, 2, 4, 5.0),
        (JobStage.RENDERING, 4, 4, 10.0),  # stage end == next boundary
        (JobStage.GENERATING_CARDS, 1, 3, 30.0),
        (JobStage.DETECTING_MASKS, 1, 2, 75.0),
        (JobStage.COMPOSING, 5, 5, 95.0),
        (JobStage.FINALIZING, 1, 1, 100.0),
        (JobStage.RENDERING, 99, 4, 10.0),  # overshoot clamps to the stage end
    ],
)
def test_percent_at_stage_boundaries(
    stage: JobStage, completed: int, total: int, expected: float
) -> None:
    percent, _ = compute_progress(_job(stage=stage, completed=completed, total=total))
    assert percent == pytest.approx(expected)


@pytest.mark.parametrize("total", [None, 0, -3])
def test_unknown_denominator_yields_nothing(total: int | None) -> None:
    job = _job(stage=JobStage.DETECTING_MASKS, completed=2, total=total, elapsed_seconds=60)
    assert compute_progress(job) == (None, None)


def test_no_stage_yields_nothing() -> None:
    assert compute_progress(_job(stage=None, total=4)) == (None, None)


def test_eta_is_emitted_above_the_fraction_floor() -> None:
    # generating_cards 1/3 -> 30% complete, 30s elapsed -> ~70s remaining.
    job = _job(
        stage=JobStage.GENERATING_CARDS, completed=1, total=3, elapsed_seconds=30
    )
    percent, eta = compute_progress(job)
    assert percent == pytest.approx(30.0)
    assert eta is not None
    assert 65 <= eta <= 75


def test_eta_suppressed_below_the_fraction_floor() -> None:
    # rendering 1/4 -> 2.5% complete, below the 15% floor.
    job = _job(stage=JobStage.RENDERING, completed=1, total=4, elapsed_seconds=30)
    percent, eta = compute_progress(job)
    assert percent == pytest.approx(2.5)
    assert eta is None


def test_eta_requires_a_start_time() -> None:
    job = _job(stage=JobStage.GENERATING_CARDS, completed=1, total=3)
    percent, eta = compute_progress(job)
    assert percent == pytest.approx(30.0)
    assert eta is None


def test_eta_requires_positive_elapsed_time() -> None:
    # A clock skew that puts the start time in the future must not yield an ETA.
    job = _job(stage=JobStage.GENERATING_CARDS, completed=1, total=3, elapsed_seconds=-60)
    assert compute_progress(job)[1] is None


def test_naive_start_time_is_treated_as_utc() -> None:
    job = _job(stage=JobStage.GENERATING_CARDS, completed=1, total=3)
    job.processing_started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    percent, eta = compute_progress(job)
    assert percent == pytest.approx(30.0)
    assert eta is not None


def test_ready_job_is_exactly_one_hundred() -> None:
    job = _job(status=JobStatus.READY, stage=JobStage.FINALIZING, completed=1, total=1)
    assert compute_progress(job) == (100.0, None)


def test_failed_job_reports_nothing() -> None:
    job = _job(status=JobStatus.FAILED, stage=JobStage.RENDERING, completed=2, total=4)
    assert compute_progress(job) == (None, None)


def test_pending_job_reports_nothing() -> None:
    job = _job(status=JobStatus.PENDING, stage=None)
    assert compute_progress(job) == (None, None)


def test_legacy_null_stage_completed_does_not_raise() -> None:
    job = _job(stage=JobStage.EXTRACTING, completed=None, total=5)
    percent, eta = compute_progress(job)
    assert percent == pytest.approx(10.0)
    assert eta is None


def test_percent_is_monotonic_across_a_stage_sequence() -> None:
    sequence: list[tuple[JobStage, int, int]] = [
        (JobStage.RENDERING, 0, 4),
        (JobStage.RENDERING, 2, 4),
        (JobStage.RENDERING, 4, 4),
        (JobStage.EXTRACTING, 0, 4),
        (JobStage.EXTRACTING, 4, 4),
        (JobStage.GENERATING_CARDS, 0, 2),
        (JobStage.GENERATING_CARDS, 2, 2),
        (JobStage.DETECTING_MASKS, 0, 3),
        (JobStage.DETECTING_MASKS, 3, 3),
        (JobStage.COMPOSING, 0, 6),
        (JobStage.COMPOSING, 6, 6),
        (JobStage.FINALIZING, 0, 1),
        (JobStage.FINALIZING, 1, 1),
    ]
    previous = -1.0
    for stage, completed, total in sequence:
        percent, _ = compute_progress(_job(stage=stage, completed=completed, total=total))
        assert percent is not None
        assert percent >= previous
        previous = percent
    assert previous == pytest.approx(100.0)


class _ExplodingSession:
    """A stand-in Session whose every operation raises."""

    def get(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    def add(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    def commit(self) -> None:
        raise RuntimeError("boom")


def test_reporter_never_propagates_failures() -> None:
    progress = JobProgress(_ExplodingSession(), 1)  # type: ignore[arg-type]
    progress.stage(JobStage.RENDERING, total=3)
    progress.advance()
    progress.advance(5)


def test_reporter_persists_stage_and_counters() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        job = Job(deck_name="Deck", pdf_path="")
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.id is not None

        progress = JobProgress(session, job.id)
        progress.stage(JobStage.RENDERING, total=3)
        session.refresh(job)
        assert job.stage == JobStage.RENDERING
        assert job.stage_total == 3
        assert job.stage_completed == 0

        progress.advance()
        progress.advance()
        session.commit()  # advance() is rate-limited; force the pending state out
        session.refresh(job)
        assert job.stage_completed == 2

        # A new stage always commits and resets the counter.
        progress.stage(JobStage.EXTRACTING, total=None)
        session.refresh(job)
        assert job.stage == JobStage.EXTRACTING
        assert job.stage_completed == 0
        assert job.stage_total is None


def test_reporter_ignores_a_missing_job() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        progress = JobProgress(session, 999)
        progress.stage(JobStage.RENDERING, total=1)
        progress.advance()
