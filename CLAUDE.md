# Project Instructions

## What This Is
study-buster (Anki Notes Generator) is a local-first app: upload an annotated
lecture PDF → background processing renders pages and generates flashcard
drafts with a vision LLM → review/edit/delete drafts in the browser → export
an `.apkg` deck for Anki. Single-user, local-only; no accounts or cloud
deployment.

## Stack
- Frontend: React, TypeScript, Vite, and Mantine.
- Frontend state: Redux Toolkit for shared client state and RTK Query for server state.
- Backend: typed Python (uv-managed), FastAPI, Pydantic, SQLModel, SQLite.
- PDF: PyMuPDF for page rendering and supplemental text extraction.
- Card generation: `CardGenerator` protocol behind `CARD_GENERATOR=anthropic|mock`.
  `AnthropicCardGenerator` uses `claude-haiku-4-5` via structured outputs
  (`client.messages.parse()`); `MockCardGenerator` is the token-free default
  for dev and tests.
- Diagram detection: local Apple Vision OCR (`ocrmac`, macOS-only) supplies
  label-text geometry; a double-pass `claude-haiku-4-5` classifier picks which
  OCR items are diagram labels. Crops are derived deterministically
  (whitespace-snap). No structure-level boxes; the target's label mask is the
  highlight.
- Export: `genanki`, with rendered page images attached as media.
- Tests: use the test frameworks already configured in each package (see Commands).

## Repository Map
- `frontend/`: browser application (`src/api`, `src/components`, `src/pages`, `src/types`, `src/app`).
- `backend/`: FastAPI app and document-processing logic (`app/api`, `app/models`, `app/schemas`, `app/services`, `app/storage`).
- `backend/app/services/document_processing/`: PyMuPDF rendering + text extraction.
- `backend/app/services/card_generation/`: generator protocol, mock/Anthropic implementations, page grouping, schemas.
- `backend/app/services/anki_export/`: `.apkg` builder, note/deck models, naming.
- `docs/api-contract.md`: canonical, typed frontend/backend contract — propose contract changes there first.
- `scripts/dev.sh`: local dev orchestration for both servers (see `run` skill).

## Architecture
```text
React (RTK Query) -> FastAPI
                        +-- SQLite: Jobs, CardDrafts (SQLModel)
                        +-- Local storage: PDFs, rendered page images, exports
                        +-- Background task -> processing pipeline:
                              render pages (PyMuPDF)
                              -> extract supplemental text
                              -> group ~10 pages/call -> CardGenerator
                              -> validate + cross-group dedup
                              -> save drafts, mark Job ready
                              -> (on export) build .apkg with media
```
- FastAPI routes validate, persist, and start jobs; they hold no
  document-processing logic — that lives in plain Python services.
- The model does content understanding only, one structured-output call per
  page group. Rendering, chunking, text extraction, validation, dedup, CRUD,
  and packaging are all deterministic Python.

## Data Model
- **Job**: `id`, `deck_name`, `pdf_path`, `page_count`, `status`
  (pending/processing/ready/failed), `error_message`, timestamps.
- **CardDraft**: `id`, `job_id`, `note_type` (basic|cloze), `front`, `back`,
  `cloze_text`, `source_page`, `needs_page_image`, `is_deleted`, timestamps.
  `needs_page_image` means the rendered `source_page` image is attached as
  card media at export and shown as a preview in Review.

## API Contract (see `docs/api-contract.md` for full detail)
- `POST /jobs` — upload deck name + PDF; creates Job, starts background processing.
- `GET /jobs/{job_id}` — status, error, page/card counts; frontend polls ~3s.
- `GET /jobs/{job_id}/cards` — non-deleted card drafts.
- `GET /jobs/{job_id}/pages/{page_number}` — rendered page image for preview.
- `PUT /cards/{card_id}` — update one draft (note-type switch auto-clears
  now-irrelevant fields server-side).
- `DELETE /cards/{card_id}` — soft-delete one draft.
- `POST /jobs/{job_id}/export` — synchronously build and stream the `.apkg`.

## Frontend Screens
- **Upload**: deck name, single PDF upload, validation messages, Generate.
- **Processing**: status, activity indicator, failure message, polling.
- **Review**: editable card list, note-type label, source page, page-image
  preview when `needs_page_image`, save/delete, Export.

## Capabilities Verified So Far
- Full workflow runs end-to-end in mock mode and in real mode
  (`claude-haiku-4-5`): upload → background processing → review/edit/delete →
  `.apkg` export.
- A 7-slide annotated deck produced 19 relevant Basic cards in real mode;
  page-image preview renders for flagged cards; export succeeds.
- Cloze note type is supported end-to-end (schema, validation, export), but
  the real model has so far only produced Basic cards on the test set.

## Future Work
- Full mask editor for image-occlusion cards in Review (drag/resize/add/remove
  masks on a canvas overlay); the image-card MVP is preview + accept/delete only.

## Working Rules
- Inspect nearby code before introducing a new pattern.
- Keep frontend and backend contracts explicit and typed.
- Prefer focused changes over unrelated cleanup.
- Do not add dependencies without explaining why existing tools are insufficient.
- Run targeted validation after edits; expand to the full suite before release-sized changes.
- Keep the main Sonnet session responsible for architecture and integration.
- Delegate only bounded, context-heavy work to subagents.
- Define or approve API contracts before frontend and backend agents depend on them.
- Run frontend and backend agents sequentially unless their file ownership is clearly separate.
- Use the researcher only for external or version-sensitive uncertainty.
- Use the test engineer for routine focused tests; return architecturally complex tests to the main agent.

## Frontend State
- Prefer RTK Query for fetching, caching, invalidation, and server-state synchronization.
- Use Redux Toolkit slices only for genuinely shared client state.
- Keep component-local UI and form state local.
- Reuse the established store, base query, tags, and error-handling conventions.
- Do not introduce another global state library without explicit justification.

## Commands
- Run both dev servers (mock mode, no API key needed): use the `run` skill, or `./scripts/dev.sh up` / `down` / `status` / `logs`.
- Real-mode testing (real card generator + diagram detector, hits the Anthropic API): `CARD_GENERATOR=anthropic DIAGRAM_DETECTOR=anthropic ANTHROPIC_API_KEY=… ./scripts/dev.sh up` (or `down` then the harness-tracked `fg-backend`/`fg-frontend` per the `run` skill). `CARD_GENERATOR` alone only switches text-card generation (Haiku) — diagram/mask detection (local OCR + Haiku classifier) is gated separately by `DIAGRAM_DETECTOR` and defaults to mock. `ANTHROPIC_API_KEY` lives in `~/.zshrc`; source it rather than hardcoding it in a command.
- Frontend install: `cd frontend && npm install`
- Frontend dev only: `cd frontend && npm run dev`
- Frontend checks (lint, typecheck, test, build): `cd frontend && ./check.sh`
- Backend install: `cd backend && uv sync`
- Backend dev only: `cd backend && uv run uvicorn app.main:app --reload`
- Backend checks (ruff, mypy, pytest): `cd backend && ./check.sh`

## Documentation
- Keep this file below 200 lines.
- Store detailed repeatable procedures in `.claude/skills/`, not here.
- Update this file only for durable architecture, commands, conventions, and workflows.
- Run `/sync-claude-md` manually after meaningful structural changes; do not treat this file as a changelog.
