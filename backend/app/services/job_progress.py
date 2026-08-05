"""Job progress reporting and derivation (generation-features-plan §4).

Two responsibilities live here:

- `JobProgress`, the pipeline-side reporter that persists the current stage and
  its counters. Every public method swallows exceptions: progress reporting can
  never fail a job.
- Pure computation (`stage_label`, `compute_progress`) used by the API layer so
  the frontend never re-derives stage weights.
"""

import time
from datetime import UTC, datetime

from sqlmodel import Session

from app.models import Job, JobStage, JobStatus

#: Stage weights, in enum order. Sums to exactly 100.
STAGE_WEIGHTS: dict[JobStage, float] = {
    JobStage.RENDERING: 10.0,
    JobStage.EXTRACTING: 5.0,
    JobStage.GENERATING_CARDS: 45.0,
    JobStage.DETECTING_MASKS: 30.0,
    JobStage.COMPOSING: 5.0,
    JobStage.FINALIZING: 5.0,
}

STAGE_LABELS: dict[JobStage, str] = {
    JobStage.RENDERING: "Rendering pages",
    JobStage.EXTRACTING: "Extracting text",
    JobStage.GENERATING_CARDS: "Generating cards",
    JobStage.DETECTING_MASKS: "Finding diagram labels",
    JobStage.COMPOSING: "Composing card images",
    JobStage.FINALIZING: "Finishing up",
}

#: Minimum completed fraction before an ETA is honest enough to show.
ETA_MIN_FRACTION = 0.15

#: Minimum seconds between progress commits while advancing within a stage.
_COMMIT_INTERVAL_SECONDS = 1.0


def stage_weight(stage: JobStage) -> float:
    return STAGE_WEIGHTS[stage]


def start_boundary(stage: JobStage) -> float:
    """Percentage already complete when `stage` begins."""
    total = 0.0
    for candidate in JobStage:
        if candidate is stage:
            break
        total += STAGE_WEIGHTS[candidate]
    return total


def stage_label(stage: JobStage | None) -> str | None:
    """Human-readable label for a stage; `None` iff `stage` is `None`."""
    if stage is None:
        return None
    return STAGE_LABELS[stage]


def compute_progress(job: Job) -> tuple[float | None, int | None]:
    """Return `(progress_percent, eta_seconds)` for a job.

    Both are `None` rather than guessed whenever the current stage's denominator
    is unknown. `ready` is always exactly 100; `pending`/`failed` are always
    `None`.
    """
    if job.status == JobStatus.READY:
        return 100.0, None
    if job.status == JobStatus.FAILED:
        return None, None
    if job.status == JobStatus.PENDING:
        return None, None

    stage = job.stage
    if stage is None:
        return None, None

    total = job.stage_total
    if total is None or total <= 0:
        return None, None

    completed = min(job.stage_completed or 0, total)
    percent = start_boundary(stage) + stage_weight(stage) * completed / total
    percent = max(0.0, min(100.0, percent))

    fraction = percent / 100.0
    eta: int | None = None
    started = job.processing_started_at
    if fraction >= ETA_MIN_FRACTION and started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if elapsed > 0:
            eta = int(round(elapsed / fraction * (1 - fraction)))
    return percent, eta


class JobProgress:
    """Writes pipeline progress onto the `Job` row.

    Holds the pipeline's own `Session`. Every method is best-effort: any failure
    is swallowed so progress reporting can never fail a job. Note that no handler
    calls `session.rollback()` — the session is shared with the pipeline and a
    rollback would discard real work.
    """

    def __init__(self, session: Session, job_id: int) -> None:
        self._session = session
        self._job_id = job_id
        self._last_commit = 0.0

    def stage(self, name: JobStage, total: int | None = None) -> None:
        """Enter a stage, resetting its counter. Always commits."""
        try:
            job = self._session.get(Job, self._job_id)
            if job is None:
                return
            job.stage = name
            job.stage_completed = 0
            job.stage_total = total
            job.updated_at = datetime.now(UTC)
            self._session.add(job)
            self._session.commit()
            self._last_commit = time.monotonic()
        except Exception:
            pass

    def advance(self, n: int = 1) -> None:
        """Increment the current stage's counter, committing at most ~1x/second."""
        try:
            job = self._session.get(Job, self._job_id)
            if job is None:
                return
            job.stage_completed = (job.stage_completed or 0) + n
            job.updated_at = datetime.now(UTC)
            self._session.add(job)
            now = time.monotonic()
            if now - self._last_commit >= _COMMIT_INTERVAL_SECONDS:
                self._session.commit()
                self._last_commit = now
        except Exception:
            pass
