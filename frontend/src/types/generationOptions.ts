/**
 * Which text stage runs for a job. `basic_cloze` generates basic/cloze cards
 * with the card generator; `text_occlusion` instead masks phrases in the
 * slide's own text. Mutually exclusive.
 */
export type TextCardMode = 'basic_cloze' | 'text_occlusion'

/**
 * How the detected masks of ONE occlusion kind become cards. `individual`
 * emits one card per mask; `grouped` emits one card per page, hiding every
 * mask of that kind on the page together and revealing every answer together.
 */
export type MaskGrouping = 'individual' | 'grouped'

/**
 * The per-job choices made on the Upload page before generating. Sent with
 * `POST /jobs` and echoed, always fully populated, on every job response.
 * Options are per-job and immutable — there is no settings resource.
 */
export interface GenerationOptions {
  text_card_mode: TextCardMode
  /** Whether labeled-diagram detection runs at all; independent of the mode. */
  diagram_occlusion_enabled: boolean
  /** Grouping for diagram masks; inert unless `diagram_occlusion_enabled`. */
  diagram_mask_grouping: MaskGrouping
  /** Grouping for text masks; inert unless the mode is `text_occlusion`. */
  text_mask_grouping: MaskGrouping
}

/** The documented server-side defaults, mirrored for the initial form value. */
export const DEFAULT_GENERATION_OPTIONS: GenerationOptions = {
  text_card_mode: 'basic_cloze',
  diagram_occlusion_enabled: true,
  diagram_mask_grouping: 'individual',
  text_mask_grouping: 'individual',
}

/** The pre-split shape: one `mask_grouping` that governed both kinds. */
interface LegacyGenerationOptions {
  mask_grouping?: MaskGrouping
}

/**
 * Coerce a persisted options value into the current shape.
 *
 * The last-used options live in localStorage, so a value written before the
 * per-kind grouping split is still on disk for anyone who used the previous
 * build. Its single `mask_grouping` is expanded to both keys — mirroring what
 * the backend does for stored jobs — so the form never renders an unset
 * control or posts a partial object.
 */
export function normalizeGenerationOptions(
  stored: Partial<GenerationOptions> & LegacyGenerationOptions,
): GenerationOptions {
  const { mask_grouping: legacy, ...rest } = stored
  return {
    ...DEFAULT_GENERATION_OPTIONS,
    ...(legacy
      ? { diagram_mask_grouping: legacy, text_mask_grouping: legacy }
      : {}),
    ...rest,
  }
}
