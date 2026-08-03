export type NoteType = 'basic' | 'cloze' | 'diagram'

/** Page-normalized box: floats in `[0, 1]` over the full rendered page. */
export interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** Image-occlusion geometry for a `diagram` card. Read-only in Phase 1. */
export interface Occlusion {
  direction: 'identify' | 'locate'
  label: string
  crop_box: Box
  label_box: Box
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
 */
export interface UpdateCardRequest {
  note_type?: NoteType
  front?: string | null
  back?: string | null
  cloze_text?: string | null
  needs_page_image?: boolean
}
