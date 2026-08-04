export type JobStatus = 'pending' | 'processing' | 'ready' | 'failed'

export interface Job {
  id: number
  deck_name: string
  status: JobStatus
  error_message: string | null
  page_count: number | null
  card_count: number
  created_at: string
  updated_at: string
}
