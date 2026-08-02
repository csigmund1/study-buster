# Anki Notes Generator (study-buster) — Phase 1 Plan

## 1. Objective
Build a local-first MVP that proves:
`Upload annotated lecture PDF → Process pages → Review cards → Export .apkg → Import into Anki`
Phase 1 validates the workflow; it is not production-ready.

## Status (updated 2026-08-01)

**Built and verified in mock mode: milestones M1–M5.** The full workflow runs end-to-end — upload → background processing → review/edit/delete → export `.apkg` — using the mock card generator (no API cost).

- **M1** backend (uv + FastAPI, `/health`, CORS) and frontend (Vite + Mantine + Redux/RTK Query) skeletons; both `check.sh` gates green.
- **M2 + M4** all six contract endpoints, SQLModel storage, background task, card CRUD with note-type rules.
- **M3** pipeline: PyMuPDF page rendering + selectable-text extraction, `CardGenerator` protocol (`MockCardGenerator` default, `AnthropicCardGenerator` using `claude-haiku-4-5` structured outputs), ~10-page grouping, validation + cross-group dedup.
- **M5** export: `genanki`, stable deck/model IDs, page-image media for flagged cards, streamed `.apkg`.
- **Tests/checks:** backend 40 pytest + ruff + mypy clean; frontend 20 vitest + oxlint + tsc + build clean. Verified live: real upload → mock process → edit/delete → export a valid `.apkg` (Anki collection + `sb-job{id}-page{n}.png` media).
- **Tooling:** `uv` + Node installed via Homebrew; run/restart via the `/run` skill and `scripts/dev.sh`; PostToolUse lint hook + `docs/api-contract.md` agent rules added.

**Decisions made during build:**
- Frontend linter is **oxlint** (shipped by the Vite template), not eslint.
- On a card note-type switch, the **server auto-clears** now-irrelevant fields (documented in `docs/api-contract.md`).

**Next: real-mode testing (part of M6).** Everything above uses the mock generator, so **card quality** and **real Anki import** are still unverified — those are the remaining Phase-1 gates. Immediate steps:
1. Run in real mode (`CARD_GENERATOR=anthropic` + `ANTHROPIC_API_KEY`) against ≥3 representative annotated lecture PDFs.
2. Evaluate generated cards against §9 card rules; tune the prompt in `backend/app/services/card_generation/anthropic_generator.py`.
3. Import each exported `.apkg` into Anki on Mac and iPad; confirm text, Cloze, and slide-image media render.
4. Remaining M6 hardening (clearer errors, page/size edge cases, dedup tuning); document setup + limitations.

Not started: the post-M1 tooling checklist (§18) — `/sync-claude-md`, `/fewer-permission-prompts`.

## 2. Success Criteria
A user can:
- Open the app in a browser on the Mac running the servers (iPad support deferred).
- Upload one lecture PDF: slides with handwritten notes written on top, 5–100 pages.
- Start a non-blocking processing job.
- See pending, processing, ready, or failed status.
- Review, edit, and delete generated cards, with a page-image preview for cards that reference a slide visual.
- Export Basic and Cloze notes as an `.apkg` file, including page images as card media where flagged.
- Import the deck into Anki on Mac and iPad with text and media intact.

## 3. MVP Scope
### Included
- React, TypeScript, Vite, and Mantine frontend
- FastAPI backend with plain Python processing services
- One upload session represented as one Job; one PDF per Job
- Vision-based card generation from rendered pages (handwriting cannot be text-extracted)
- Basic and Cloze note types
- Editable drafts with source-page references and optional page-image attachment
- SQLite and local file storage
- FastAPI background task
- `.apkg` export with `genanki`, including media
- Mock model mode for zero-cost development and testing

### Excluded
- Accounts, authentication, cloud deployment, and billing
- Courses, lecture libraries, sharing, and collaboration
- Separate image uploads (input is a single PDF)
- Job listing/recovery (`GET /jobs`) and job retry — deferred
- Direct Anki sync, AnkiConnect, and image occlusion
- Figure-level bounding-box cropping (full-page images only in Phase 1)
- Advanced OCR, vector databases, and production job queues

## 4. Stack (decided)
**Frontend:** React, TypeScript, Vite, Mantine, React Router, Redux Toolkit + RTK Query
**Backend:** Python managed with **uv** (`pyproject.toml`, `uv run <cmd>`), FastAPI, Pydantic, **SQLModel**, SQLite
**PDF:** **PyMuPDF** — page rendering (primary) and selectable-text extraction (supplemental)
**Model:** Anthropic Python SDK, **`claude-haiku-4-5`** ($1/$5 per MTok), structured outputs via `client.messages.parse()` with a Pydantic card-draft schema
**Export:** `genanki` with media, local filesystem storage
**Migrations:** none in Phase 1 — `SQLModel.metadata.create_all()`; delete the SQLite file on schema change

## 5. Architecture
```text
React Web App (RTK Query)
    |
    v
FastAPI
    +-- SQLite: Jobs and CardDrafts (SQLModel)
    +-- Local storage: PDFs, rendered page images, exports
    +-- Background task
            |
            v
      Python processing pipeline
            +-- Render pages to images (PyMuPDF)
            +-- Extract selectable text (supplemental)
            +-- Generate structured drafts (CardGenerator: anthropic | mock)
            +-- Validate and deduplicate drafts
            +-- Save results
            +-- Build .apkg with media
```

### Core Rules
- FastAPI coordinates requests; it does not contain document-processing logic. Routes validate, create records, start jobs, and return data. Plain Python services do the work.
- **The model does content understanding only** — one structured-output call per page group. Everything else (rendering, chunking, text extraction, validation, dedup, CRUD, packaging, media) is deterministic Python.
- The card-generation service sits behind a `CardGenerator` protocol with two implementations: `AnthropicCardGenerator` and `MockCardGenerator` (returns fixture drafts). Selected via `CARD_GENERATOR=anthropic|mock`; mock is the default for tests and token-free dev.

## 6. Minimal Data Model
### Job
- `id`, `deck_name`, `pdf_path`, `page_count`
- `status`: pending, processing, ready, failed
- `error_message`, `created_at`, `updated_at`
### CardDraft
- `id`, `job_id`, `note_type` (basic | cloze)
- `front`, `back`, `cloze_text`
- `source_page`, `needs_page_image`, `is_deleted`
- `created_at`, `updated_at`

Use `front`/`back` for Basic cards and `cloze_text` for Cloze cards. `needs_page_image` means the rendered `source_page` image is attached as card media at export and previewed on the Review screen.

## 7. API Contract
The canonical, typed contract lives in **`docs/api-contract.md`**. Frontend and backend work must conform to it; contract changes are proposed there first. Summary:
- `POST /jobs` — upload deck name + PDF; create Job, start processing.
- `GET /jobs/{job_id}` — status, error, page/card counts; frontend polls ~3s.
- `GET /jobs/{job_id}/cards` — non-deleted card drafts.
- `GET /jobs/{job_id}/pages/{page_number}` — rendered page image for preview.
- `PUT /cards/{card_id}` — update one draft.
- `DELETE /cards/{card_id}` — soft-delete one draft.
- `POST /jobs/{job_id}/export` — synchronously build and stream the `.apkg`.

Deferred: `GET /jobs`, `POST /jobs/{id}/retry`.

## 8. Processing Pipeline
1. Validate file type and limits (PDF only, ≤100 pages, ≤50 MB).
2. Save the original PDF; create the Job (pending).
3. Mark the Job processing (background task).
4. Render every page to an image with PyMuPDF (~1024–1536px long edge; each page ≈ up to 1,600 image tokens).
5. Extract selectable slide text per page as supplemental context (handwriting will not appear here — that is expected).
6. Split pages into groups of ~10. For each group, make one structured-output call to the model with the page images + supplemental text, returning typed card drafts (`note_type`, `front`/`back`/`cloze_text`, `source_page`, `needs_page_image`).
7. Parse responses into the CardDraft schema (Pydantic-validated by `messages.parse()`).
8. Run deterministic validation and cross-group near-duplicate removal.
9. Save valid drafts and mark the Job ready.
10. On failure, save a readable error and mark the Job failed.

Do not add OCR, figure cropping, or image matching in Phase 1.

## 9. Card Rules (prompt guidance)
Generated cards should:
- Test one main fact or relationship.
- Be understandable without reopening the notes.
- Avoid vague questions, unsupported facts, and unnecessary duplicates.
- Prefer concise answers.
- Use Cloze only when the sentence remains understandable.
- Set `needs_page_image` only when the slide's visual (diagram, chart, figure) is required to understand the card.
- Include the source page.
- No fixed card-density target — density follows the content.

## 10. Validation Rules (deterministic Python)
- Supported note type only.
- Basic cards require front and back; Cloze cards require valid `{{c1::...}}` syntax.
- Reject empty or malformed cards and extremely long fields.
- `source_page` must be within 1..`page_count`.
- Flag/remove near-duplicates (normalized-text comparison), including across page groups.

## 11. Frontend Screens
### Upload
Deck name, single PDF upload, selected-file display, Generate button, validation messages.
### Processing
Current status, activity indicator, failure message, polling every few seconds. (Restart deferred.)
### Review
Editable card list, Basic/Cloze label, source page, page-image preview when `needs_page_image`, save/delete controls, Export button. A side-by-side PDF viewer is deferred.

## 12. Repository Structure
```text
project/
├── frontend/src/
│   ├── api/ components/ pages/ types/ app/
├── backend/
│   ├── app/
│   │   ├── api/ models/ schemas/ storage/
│   │   ├── services/
│   │   │   ├── document_processing/
│   │   │   ├── card_generation/
│   │   │   └── anki_export/
│   │   └── main.py
│   └── tests/
└── docs/
    └── api-contract.md
```

## 13. Implementation Milestones
### M1 — Project Skeleton ✓ done (mock)
- Scaffold frontend (Vite + Mantine + Redux/RTK Query) and backend (uv + FastAPI).
- Health endpoint, local CORS, RTK Query base API confirmed against it.
- Wire up checks: ruff/mypy/pytest backend; eslint/prettier/tsc/vitest frontend; `check.sh` per package.
**Exit:** React displays backend data; all check commands run clean.
### M2 — Upload and Jobs ✓ done (mock)
- Upload form; save PDF locally; create Job; background task; status polling.
**Exit:** Upload creates a persistent Job whose status is visible.
### M3 — Card Generation ✓ done (mock; real-model quality untested)
- Page rendering + supplemental text extraction.
- `CardGenerator` protocol, mock implementation (fixtures) first, then Anthropic implementation.
- Validation/dedup; failure handling.
**Exit:** A representative annotated PDF produces usable drafts; the full pipeline also runs end-to-end in mock mode with zero API cost.
### M4 — Review ✓ done
- Display, edit, soft-delete drafts; page-image preview; persist to SQLite.
**Exit:** Edits survive refresh.
### M5 — Anki Export ✓ built (Anki import on Mac/iPad untested)
- Stable deck/model IDs and templates; attach page images as media for flagged cards; generate `.apkg`.
- Test import on Mac and iPad Anki.
**Exit:** Anki imports correct text, formatting, and media.
### M6 — MVP Hardening ← next (starts with real-mode testing)
- File/page limits, clearer errors, duplicate checks, focused tests.
- Test at least three representative annotated lecture PDFs.
- Document setup and limitations.
**Exit:** The full workflow succeeds repeatedly on the evaluation set.

## 14. Cost Model
Per deck (input images ≈ 1,600 tokens/page max at chosen render size; Haiku 4.5 at $1/$5 per MTok):
- ≤20 pages: ~$0.03–0.05 (meets the ≤$0.05 target)
- ~30 pages: ~$0.07–0.09
- 100 pages (worst case): ~$0.20–0.25

Cost knobs if needed: lower render resolution (largest lever), tighter prompts, later the Batch API (50% off, adds latency — not for interactive use).
Configuration: `ANTHROPIC_API_KEY` (env), `CARD_GENERATOR=anthropic|mock`.

## 15. Testing Priorities
**Backend:** upload validation, status transitions, page rendering/grouping, model-response parsing (against mock + recorded fixtures), card validation/dedup, updates/deletes, `.apkg` generation with media.
**Frontend:** upload validation, polling, failure states, editing, deletion, export download (MSW-mocked API).
**Evaluation files:** three representative annotated slide PDFs of varying length and handwriting density.

## 16. Decisions (resolved)
- PDF library: PyMuPDF (render + text).
- Model: `claude-haiku-4-5`, structured outputs, mock mode included.
- Processing: page groups of ~10; vision-first.
- Limits: ≤100 pages, ≤50 MB.
- Card density: content-driven, no fixed target.
- Export: sync build + stream (revisit only if decks outgrow it).
- Env/ORM/checks: uv, SQLModel (no Alembic), ruff + mypy + pytest (no tox), eslint + prettier + tsc + Vitest/RTL/MSW.
All service choices stay behind service interfaces.

## 17. Phase 1 Done Checklist
- Local setup is documented.
- An annotated slide PDF can be uploaded.
- Processing does not block the upload request.
- Job status and failures are visible.
- Cards can be reviewed, edited, and deleted; page previews work.
- `.apkg` export works with media.
- The deck imports into Anki on Mac and iPad.
- Three representative note sets complete successfully.
- Known limitations are documented.

## 18. Post-M1 Checklist (tooling)
- Fill the CLAUDE.md Commands placeholders (run `/sync-claude-md`).
- Run `/fewer-permission-prompts` to build the project permission allowlist.
- Add a project `run` skill (how to start both dev servers, ports, env vars).

## 19. Later Phases
Job listing/recovery, job retry, separate image uploads, figure-level cropping, Redis/RQ worker, better OCR, side-by-side source review, course organization, stable deck updates, AnkiConnect, image occlusion, authentication, cloud storage, iPad browser access over LAN.
