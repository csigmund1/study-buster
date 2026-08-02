export type NoteType = 'basic' | 'cloze'

export interface CardDraft {
  id: number
  job_id: number
  note_type: NoteType
  front: string | null
  back: string | null
  cloze_text: string | null
  source_page: number
  needs_page_image: boolean
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
