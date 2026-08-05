import type { GenerationOptions } from './generationOptions'

export type JobStatus = 'pending' | 'processing' | 'ready' | 'failed'

/** Pipeline stage currently running; `null` while the job is `pending`. */
export type JobStage =
  | 'rendering'
  | 'extracting'
  | 'generating_cards'
  | 'detecting_masks'
  | 'composing'
  | 'finalizing'

export interface Job {
  id: number
  deck_name: string
  status: JobStatus
  error_message: string | null
  page_count: number | null
  card_count: number
  stage: JobStage | null
  /** Human-readable label for `stage`; `null` iff `stage` is `null`. */
  stage_label: string | null
  /**
   * Float in `[0, 100]`, or `null` when the current stage's denominator is
   * unknown. Server-computed — never re-derived or defaulted to `0` here.
   */
  progress_percent: number | null
  /** Integer seconds remaining, or `null` when no honest ETA exists. */
  eta_seconds: number | null
  /**
   * Generation options that produced this job. Always present and fully
   * populated — the server resolves every key at creation time.
   */
  options: GenerationOptions
  created_at: string
  updated_at: string
}
