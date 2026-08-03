# API Contract (canonical)

This file is the single source of truth for the frontend/backend contract. Both sides must conform to it. To change a request/response shape, propose the change here first (main session approves), then implement.

Base URL (dev): `http://localhost:8000`. All bodies are JSON unless noted. Timestamps are ISO 8601 UTC strings.

## Error shape

FastAPI default:

```json
{ "detail": "Human-readable message" }
```

Validation errors (422) use FastAPI's standard `detail` array. The frontend surfaces `detail` when it is a string, otherwise a generic message.

## Types

### Job

```json
{
  "id": 1,
  "deck_name": "Biology Lecture 3",
  "status": "pending | processing | ready | failed",
  "error_message": null,
  "page_count": 42,
  "card_count": 0,
  "created_at": "2026-08-01T19:00:00Z",
  "updated_at": "2026-08-01T19:00:05Z"
}
```

- `page_count` is `null` until the PDF has been opened by the pipeline.
- `card_count` counts non-deleted drafts; `0` until `ready`.

### CardDraft

```json
{
  "id": 10,
  "job_id": 1,
  "note_type": "basic | cloze | diagram",
  "front": "What enzyme unwinds DNA?",
  "back": "Helicase",
  "cloze_text": null,
  "occlusion": null,
  "source_page": 7,
  "needs_page_image": false,
  "created_at": "2026-08-01T19:02:00Z",
  "updated_at": "2026-08-01T19:02:00Z"
}
```

- `basic`: `front` and `back` non-empty, `cloze_text` null, `occlusion` null.
- `cloze`: `cloze_text` non-empty with valid `{{c1::...}}` syntax; `front`/`back` null, `occlusion` null.
- `diagram`: a card generated from a labeled diagram. `front`/`back` hold the question/answer **text** (see below); `cloze_text` null; `occlusion` non-null; the composed card images are fetched via `GET /cards/{card_id}/image` and attached as media at export. `needs_page_image` is ignored for diagram cards (the composed crop, not the full page, is the media).
- `needs_page_image: true` (basic/cloze only) means the rendered `source_page` image is shown in Review and attached as media at export.

#### `occlusion` (diagram cards only)

All boxes are **page-normalized** floats in `[0, 1]` over the full rendered
`source_page` image, origin top-left: `{ left, top, width, height }`.

```json
{
  "direction": "identify | locate",
  "label": "Thyroid gland",
  "crop_box":   { "left": 0.20, "top": 0.05, "width": 0.55, "height": 0.90 },
  "label_box":  { "left": 0.61, "top": 0.32, "width": 0.15, "height": 0.05 },
  "mask_boxes": [ { "left": 0.61, "top": 0.32, "width": 0.15, "height": 0.05 } ]
}
```

- `direction`: `identify` ("What is this?" — the target's *label mask* is
  highlighted; answer lifts that mask to reveal the label) or `locate` ("What
  points to <label>?" — all labels masked; answer highlights the target's mask).
  **Phase 1 emits `identify` only; `locate` is Phase 2.** The shape does not
  change between phases.
- `crop_box`: the diagram region cropped for the card. Derived deterministically
  server-side (union of label boxes expanded to whitespace gutters), not
  model-predicted.
- `label_box`: the target label's own text box — its mask is drawn in the
  highlight style on the question side, lifted on the answer side (identify),
  and covered by the single native-IO cloze at export. There is no
  structure-level `target_box`: the label mask itself is the pointer, sitting at
  the end of the label's arrow/leader line.
- `mask_boxes`: every label's text box, including the target's (page-normalized,
  like the others); masked on the question side. Composition/export convert these
  to crop-local coordinates.
- `front`/`back` text: identify → `front` "What is this?", `back` the label;
  locate → `front` "What points to <label>?", `back` the label.

## Endpoints

### `POST /jobs`

Create a job and start background processing.

- Request: `multipart/form-data` — `deck_name` (string, 1–100 chars), `file` (PDF).
- Limits: PDF only, ≤ 100 pages, ≤ 50 MB.
- `201` → Job.
- `422` → invalid deck name, non-PDF, or over limits (page count may be enforced asynchronously → job becomes `failed`).

### `GET /jobs/{job_id}`

- `200` → Job. Frontend polls every ~3 s while `pending`/`processing`.
- `404` → unknown job.

### `GET /jobs/{job_id}/cards`

- `200` → `CardDraft[]` (non-deleted only, ordered by `source_page`, then `id`).
- `404` → unknown job.
- Returns `[]` (not an error) if the job is not `ready` yet.

### `GET /jobs/{job_id}/pages/{page_number}`

Serve the rendered page image for Review preview.

- `200` → image bytes (`image/png` or `image/jpeg` — whatever the pipeline stored), cacheable.
- `404` → unknown job, page out of range, or images not rendered yet.

### `GET /cards/{card_id}/image`

Serve a composed diagram-card image (question or answer side) for Review preview
and export.

- Query: `side` = `question` | `answer` (required).
- `200` → `image/png`, cacheable.
- `400` → missing or invalid `side`.
- `404` → unknown/deleted card, a non-`diagram` card, or composition missing.

### `PUT /cards/{card_id}`

Partial update of editable fields: `front`, `back`, `cloze_text`, `note_type`, `needs_page_image`.

- Request example: `{ "front": "…", "back": "…" }`
- When `note_type` changes, the server clears fields irrelevant to the new type (`cloze_text` on switch to `basic`; `front`/`back` on switch to `cloze`). The frontend does not need to send explicit nulls, but the switch request must include the fields the new type requires.
- **Diagram cards** cannot switch `note_type`, and their `occlusion` geometry is read-only; only `front`/`back` text is editable. A request that changes `note_type` on a diagram card, or targets `occlusion`, is `422`.
- `200` → updated CardDraft.
- `404` → unknown or deleted card.
- `422` → violates note-type rules (e.g. cloze without valid syntax; diagram note-type switch).

### `DELETE /cards/{card_id}`

Soft delete (`is_deleted = true`).

- `204` → deleted.
- `404` → unknown card. Deleting an already-deleted card is a `404`.

### `POST /jobs/{job_id}/export`

Synchronously build and stream the `.apkg` (decks are small; build is ~1–2 s). Includes page-image media for basic/cloze cards with `needs_page_image`. Diagram cards export with their composed images as media — `identify` cards as native Anki Image Occlusion notes (Anki 23.10+).

- `200` → binary body, `Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename="<deck_name-slug>.apkg"`. Frontend downloads via blob.
- `404` → unknown job.
- `409` → job not `ready`.

## Deferred (not in Phase 1)

- `GET /jobs` — job listing/recovery after refresh.
- `POST /jobs/{job_id}/retry` — restart a failed job.
- Separate image uploads (diagram cards are derived from the uploaded PDF only).
- Diagram `locate` direction — Phase 2; the `occlusion` shape already supports it.
- User-editable occlusion geometry (drag/resize/add/remove masks in Review) —
  the composed masks are fixed in the MVP; see CLAUDE.md Future Work.
