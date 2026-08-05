export type NoteType = 'basic' | 'cloze' | 'diagram' | 'text_occlusion'

/**
 * Note types backed by composed occlusion images. They behave identically:
 * fixed note type (no switching in or out), read-only geometry, `front`/`back`
 * the only editable fields, images from `GET /cards/{card_id}/image`.
 */
export function isOcclusionNoteType(noteType: NoteType): boolean {
  return noteType === 'diagram' || noteType === 'text_occlusion'
}

/**
 * Which detector produced an occlusion. Note the deliberate naming asymmetry:
 * the note type is `text_occlusion`, the occlusion kind is `text`.
 */
export type OcclusionKind = 'diagram' | 'text'

/** Page-normalized box: floats in `[0, 1]` over the full rendered page. */
export interface Box {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Occlusion geometry for a `diagram` or `text_occlusion` card. Read-only in
 * the client; the API always emits this shape (legacy rows are upgraded
 * server-side on read).
 */
export interface Occlusion {
  kind: OcclusionKind
  direction: 'identify' | 'locate'
  /** Answer text, in order; always at least one entry. */
  labels: string[]
  crop_box: Box
  /**
   * Boxes revealed on the answer side; always at least one entry. NOT parallel
   * to `labels` — a single label can span several boxes when a masked phrase
   * wraps across lines.
   */
  target_boxes: Box[]
  mask_boxes: Box[]
}

export interface CardDraft {
  id: number
  job_id: number
  note_type: NoteType
  front: string | null
  back: string | null
  cloze_text: string | null
  source_page: number
  needs_page_image: boolean
  occlusion: Occlusion | null
  created_at: string
  updated_at: string
}

/**
 * Partial update payload for `PUT /cards/{card_id}`. When `note_type`
 * changes, the request must include the fields the new type requires (the
 * server clears fields that are irrelevant to the new type on its own).
 * Occlusion note types (`diagram`, `text_occlusion`) cannot be switched into
 * or out of, and their geometry is read-only: only `front`/`back` are
 * editable, and `note_type` must be echoed back unchanged.
 */
export interface UpdateCardRequest {
  note_type?: NoteType
  front?: string | null
  back?: string | null
  cloze_text?: string | null
  needs_page_image?: boolean
}
