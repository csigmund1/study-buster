#!/usr/bin/env python3
"""Dump a job's card drafts into a self-contained review file.

Usage (from `backend/`):
    uv run python scripts/dump_occlusion_review.py <job_id>

Writes `DATA_DIR/jobs/{job_id}/occlusion_review.md`, listing every
non-deleted card draft grouped by source page: its note type, front, and
back/answer text, plus the on-disk paths to its composed question/answer
images (when those files exist) so a human can eyeball mask quality without
opening the app.
"""

import sys
from pathlib import Path

# Make `app` importable when run as `uv run python scripts/dump_occlusion_review.py`
# from `backend/`, without requiring the package to be installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import CardDraft  # noqa: E402
from app.storage import card_image_path, job_dir, session_for  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <job_id>", file=sys.stderr)
        raise SystemExit(1)
    job_id = int(sys.argv[1])

    settings = get_settings()
    session = session_for(settings)
    try:
        cards = session.exec(
            select(CardDraft)
            .where(CardDraft.job_id == job_id)
            .where(CardDraft.is_deleted == False)  # noqa: E712 - SQLAlchemy needs `== False`
        ).all()
        cards = sorted(cards, key=lambda card: (card.source_page or 0, card.id or 0))
    finally:
        session.close()

    pages: dict[int, list[CardDraft]] = {}
    for card in cards:
        pages.setdefault(card.source_page or 0, []).append(card)

    lines = [f"# Occlusion review — job {job_id}", ""]
    for page_number in sorted(pages):
        lines.append(f"## Page {page_number}")
        lines.append("")
        for card in pages[page_number]:
            assert card.id is not None
            lines.append(f"### Card {card.id} ({card.note_type})")
            lines.append(f"- Front: {card.front}")
            lines.append(f"- Back: {card.back}")

            question_path = card_image_path(settings, job_id, card.id, "question")
            if question_path.is_file():
                lines.append(f"- Question image: {question_path}")
            answer_path = card_image_path(settings, job_id, card.id, "answer")
            if answer_path.is_file():
                lines.append(f"- Answer image: {answer_path}")
            lines.append("")

    output_path = job_dir(settings, job_id) / "occlusion_review.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
