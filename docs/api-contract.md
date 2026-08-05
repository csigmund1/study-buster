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
  "stage": "rendering | extracting | generating_cards | detecting_masks | composing | finalizing | null",
  "stage_label": "Rendering pages",
  "progress_percent": 12.5,
  "eta_seconds": 45,
  "options": {
    "text_card_mode": "basic_cloze",
    "diagram_occlusion_enabled": true,
    "diagram_mask_grouping": "individual",
    "text_mask_grouping": "individual"
  },
  "created_at": "2026-08-01T19:00:00Z",
  "updated_at": "2026-08-01T19:00:05Z"
}
```

- `page_count` is `null` until the PDF has been opened by the pipeline.
- `card_count` counts non-deleted drafts; `0` until `ready`.
- `options` records the generation options that produced this job. See below.

#### Progress fields

Progress is server state: it survives a refresh, and the frontend never re-derives
it. All three derived fields are computed server-side from the job's stored stage
and counters.

- `stage` — the pipeline stage currently running. `null` while `pending`; retains
  the last stage reached once the job is `ready` or `failed`.
- `stage_label` — human-readable label for `stage`, `null` iff `stage` is `null`:

  | `stage` | `stage_label` |
  |---|---|
  | `rendering` | Rendering pages |
  | `extracting` | Extracting text |
  | `generating_cards` | Generating cards |
  | `detecting_masks` | Finding diagram labels |
  | `composing` | Composing card images |
  | `finalizing` | Finishing up |

- `progress_percent` — float in `[0, 100]`, or `null`. Never decreases over the
  life of a job. Exactly `100` when `status` is `ready`. `null` when `status` is
  `pending` or `failed`, and `null` while the current stage's denominator is
  unknown — the percentage is **absent, never guessed**. Clients must treat `null`
  as "show an indeterminate indicator", not as `0`.
- `eta_seconds` — integer seconds remaining, or `null`. Emitted **only** when the
  overall completed fraction is at least `0.15` **and** the current stage has a
  known denominator; `null` otherwise, and always `null` when `status` is
  `pending`, `ready`, or `failed`. Clients should render it coarsely (e.g.
  "about 2 min remaining") rather than to the second.

#### `options` — generation options

The three per-job choices made on the Upload page before generating. They are
recorded with the job and echoed on every job response.

```json
{
  "text_card_mode": "basic_cloze | text_occlusion",
  "diagram_occlusion_enabled": true,
  "diagram_mask_grouping": "individual | grouped",
  "text_mask_grouping": "individual | grouped"
}
```

- `text_card_mode` — which text stage runs. `basic_cloze` generates basic/cloze
  cards with the card generator; `text_occlusion` instead masks phrases in the
  slide's own text. Mutually exclusive. Default `basic_cloze`.
- `diagram_occlusion_enabled` — whether labeled-diagram detection runs at all.
  Independent of `text_card_mode`. Default `true`.
- `diagram_mask_grouping` / `text_mask_grouping` — how detected masks become
  cards, chosen **independently for each occlusion kind**. `individual` emits one
  card per mask; `grouped` emits **one card per page**, hiding every mask of that
  kind on the page together and revealing every answer together. Each defaults to
  `individual`. The two are unrelated: `diagram_mask_grouping: "individual"` with
  `text_mask_grouping: "grouped"` is a valid and meaningful combination.

A grouping key only has an effect when its kind can produce masks —
`diagram_mask_grouping` requires `diagram_occlusion_enabled: true`, and
`text_mask_grouping` requires `text_card_mode: "text_occlusion"`. An inert value
is still accepted, stored, and echoed back verbatim; it is never rewritten.

**Legacy key.** Options persisted before the split used a single
`mask_grouping` that applied to both kinds. It is upgraded on read
(`mask_grouping` → both `diagram_mask_grouping` and `text_mask_grouping`), so the
API always emits the shape above and jobs created before the split keep working
with no database reset. A request may still send `mask_grouping`, with the same
meaning; if it is sent alongside either specific key, the specific key wins and
the legacy key only fills the one left unspecified.

`options` is **always present and fully populated** on a job response — the
server resolves every key at creation time and stores the result, so clients
never see `null` and never re-derive a default. Jobs created before this field
existed report the documented defaults.

The defaults above are what a stock deployment resolves an unspecified key to.
`text_card_mode` additionally honours the server's `TEXT_CARD_MODE` environment
variable as its default when the key is absent, so a backend can be run in
text-occlusion mode without a UI. This only affects *unspecified* keys; a key the
client sends always wins, and the frontend always sends all three.

Options are per-job and immutable: there is no settings resource, no settings
endpoint, and no way to change a job's options after creation. The client is
responsible for remembering the user's last choices locally.

### CardDraft

```json
{
  "id": 10,
  "job_id": 1,
  "note_type": "basic | cloze | diagram | text_occlusion",
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
- `diagram`: a card generated from a labeled diagram. `front`/`back` hold the question/answer **text** (see below); `cloze_text` null; `occlusion` non-null with `kind: "diagram"`; the composed card images are fetched via `GET /cards/{card_id}/image` and attached as media at export. `needs_page_image` is ignored (the composed crop, not the full page, is the media).
- `text_occlusion`: a fill-in-the-blank card over the rendered page, masking a phrase in the slide's own text. `front` is the prompt (`"Fill in the blank"`), `back` the masked phrase; `cloze_text` null; `occlusion` non-null with `kind: "text"`. Composed images and export media work exactly as for `diagram`. `needs_page_image` is ignored.
- `needs_page_image: true` (basic/cloze only) means the rendered `source_page` image is shown in Review and attached as media at export.

`diagram` and `text_occlusion` are collectively the **occlusion note types**. They behave identically across every endpoint: fixed note type, read-only geometry, `front`/`back` the only editable fields, composed images at `GET /cards/{card_id}/image`.

#### `occlusion` (occlusion cards only)

All boxes are **page-normalized** floats in `[0, 1]` over the full rendered
`source_page` image, origin top-left: `{ left, top, width, height }`.

```json
{
  "kind": "diagram | text",
  "direction": "identify | locate",
  "labels": ["Thyroid gland"],
  "crop_box":     { "left": 0.20, "top": 0.05, "width": 0.55, "height": 0.90 },
  "target_boxes": [ { "left": 0.61, "top": 0.32, "width": 0.15, "height": 0.05 } ],
  "mask_boxes":   [ { "left": 0.61, "top": 0.32, "width": 0.15, "height": 0.05 } ]
}
```

- `kind`: which detector produced this occlusion. `diagram` = a labeled-diagram
  card (note type `diagram`); `text` = a fill-in-the-blank text card (note type
  `text_occlusion`). Note the deliberate naming asymmetry: the note type is
  `text_occlusion`, the occlusion kind is `text`.
- `direction`: meaningful for `kind: "diagram"` only. `identify` ("What is
  this?" — the target's *label mask* is highlighted; the answer lifts that mask
  to reveal the label) or `locate` ("What points to <label>?"). `text`
  occlusions always carry `identify`. **Only `identify` is emitted today;
  `locate` remains deferred.**
- `labels`: the answer text, in order, always at least one entry. Under
  `individual` grouping for this occlusion's kind it is a single label or masked
  phrase; under `grouped` it carries every answer of that kind on the page, in
  reading order.
- `crop_box`: the region cropped for the card image. For `diagram`, derived
  deterministically server-side (union of label boxes expanded to whitespace
  gutters). For `text`, the **full page** — text cards are not cropped in v1.
  Never model-predicted.
- `target_boxes`: the boxes revealed on the answer side and clozed at export,
  parallel in order to `labels`. A single target may span several boxes when a
  masked phrase wraps across lines, so `target_boxes` is **not** required to be
  the same length as `labels`.
- `mask_boxes`: every box hidden on the question side — for `diagram`, every
  label's text box on the page including the target's; for `text`, the masked
  span's boxes. Composition/export convert these to crop-local coordinates.
- `front`/`back` text depends on `kind` and that kind's grouping
  (`diagram_mask_grouping` for `diagram`, `text_mask_grouping` for `text`):

  | kind | grouping | `front` | `back` |
  |---|---|---|---|
  | `diagram` | individual | What is this? | the label |
  | `diagram` | grouped | Name all labeled parts | every label, `", "`-joined |
  | `text` | individual | Fill in the blank | the masked phrase |
  | `text` | grouped | Fill in the blanks | every masked phrase, `", "`-joined |

  A grouped card is still exactly **one** Anki card: every box in `target_boxes`
  is emitted under a single `c1` index at export.

**Legacy shape.** Occlusions persisted before this change used `label: string`
and `label_box: Box` with no `kind`. Those rows are upgraded on read
(`label` → `labels: [label]`, `label_box` → `target_boxes: [label_box]`,
`kind` defaulting to `diagram`), so **the API always emits the shape above** and
clients never see the legacy form. Jobs created before this change keep working
with no database reset.

## Endpoints

### `POST /jobs`

Create a job and start background processing.

- Request: `multipart/form-data` — `deck_name` (string, 1–100 chars), `file` (PDF),
  `options` (optional JSON **string**).
- `options` is the generation-options object documented under Job, serialized with
  `JSON.stringify` and sent as a form field. Every key is optional; omitted keys
  are resolved server-side, and omitting `options` entirely is exactly equivalent
  to sending `{}`. Unknown keys are rejected. The resolved object is persisted and
  returned.
- Limits: PDF only, ≤ 100 pages, ≤ 50 MB.
- `201` → Job, with `options` fully populated.
- `422` → invalid deck name, non-PDF, over limits (page count may be enforced
  asynchronously → job becomes `failed`), or malformed `options` — unparseable
  JSON, a non-object, an unknown key, or a value outside the documented enum.

### `GET /jobs/{job_id}`

- `200` → Job. Frontend polls every ~2 s while `pending`/`processing`, and reads
  `stage_label` / `progress_percent` / `eta_seconds` for the processing screen.
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

Serve a composed occlusion-card image (question or answer side) for Review
preview and export. Serves both `diagram` and `text_occlusion` cards.

- Query: `side` = `question` | `answer` (required).
- `200` → `image/png`, cacheable.
- `400` → missing or invalid `side`.
- `404` → unknown/deleted card, a card that is not an occlusion note type, or
  composition missing.

### `PUT /cards/{card_id}`

Partial update of editable fields: `front`, `back`, `cloze_text`, `note_type`, `needs_page_image`.

- Request example: `{ "front": "…", "back": "…" }`
- When `note_type` changes, the server clears fields irrelevant to the new type (`cloze_text` on switch to `basic`; `front`/`back` on switch to `cloze`). The frontend does not need to send explicit nulls, but the switch request must include the fields the new type requires.
- **Occlusion cards** (`diagram`, `text_occlusion`) cannot switch `note_type`, and their `occlusion` geometry is read-only; only `front`/`back` text is editable. A request that changes `note_type` on an occlusion card, that switches a non-occlusion card *into* an occlusion type, or that targets `occlusion`, is `422`.
- `200` → updated CardDraft.
- `404` → unknown or deleted card.
- `422` → violates note-type rules (e.g. cloze without valid syntax; occlusion note-type switch).

### `DELETE /cards/{card_id}`

Soft delete (`is_deleted = true`).

- `204` → deleted.
- `404` → unknown card. Deleting an already-deleted card is a `404`.

### `POST /jobs/{job_id}/export`

Synchronously build and stream the `.apkg` (decks are small; build is ~1–2 s). Includes page-image media for basic/cloze cards with `needs_page_image`. Occlusion cards (`diagram` and `text_occlusion`) export with their composed images as media, as native Anki Image Occlusion notes (Anki 23.10+). Every box in `target_boxes` is emitted under a single `c1` index, so one occlusion note always yields exactly **one** Anki card regardless of how many boxes it carries.

- `200` → binary body, `Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename="<deck_name-slug>.apkg"`. Frontend downloads via blob.
- `404` → unknown job.
- `409` → job not `ready`.

## Deferred

- `GET /jobs` — job listing/recovery after refresh.
- `POST /jobs/{job_id}/retry` — restart a failed job.
- Separate image uploads (occlusion cards are derived from the uploaded PDF only).
- Diagram `locate` direction — the `occlusion` shape already supports it.
- A settings resource: no `GET/PUT /settings`, no settings table, no per-card
  option overrides, and no way to re-run an existing job under different options.
- User-editable occlusion geometry (drag/resize/add/remove masks in Review) —
  the composed masks are fixed in the MVP; see CLAUDE.md Future Work.
