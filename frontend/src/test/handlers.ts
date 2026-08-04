import { http, HttpResponse } from 'msw'
import type { Job } from '../types/job'
import type { CardDraft, Occlusion } from '../types/card'

export const API_URL = 'http://localhost:8000'

export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    deck_name: 'Biology Lecture 3',
    status: 'ready',
    error_message: null,
    page_count: 12,
    card_count: 2,
    created_at: '2026-08-01T19:00:00Z',
    updated_at: '2026-08-01T19:00:05Z',
    ...overrides,
  }
}

export function makeCard(overrides: Partial<CardDraft> = {}): CardDraft {
  return {
    id: 10,
    job_id: 1,
    note_type: 'basic',
    front: 'What enzyme unwinds DNA?',
    back: 'Helicase',
    cloze_text: null,
    source_page: 7,
    needs_page_image: false,
    occlusion: null,
    created_at: '2026-08-01T19:02:00Z',
    updated_at: '2026-08-01T19:02:00Z',
    ...overrides,
  }
}

export function makeOcclusion(overrides: Partial<Occlusion> = {}): Occlusion {
  return {
    direction: 'identify',
    label: 'Helicase',
    crop_box: { left: 0.2, top: 0.05, width: 0.55, height: 0.9 },
    label_box: { left: 0.61, top: 0.32, width: 0.15, height: 0.05 },
    mask_boxes: [{ left: 0.61, top: 0.32, width: 0.15, height: 0.05 }],
    ...overrides,
  }
}

/** A tiny 1x1 transparent PNG, used as a stub image response in tests. */
const STUB_PNG_BYTES = Uint8Array.from(
  atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  ),
  (char) => char.charCodeAt(0),
)

/**
 * Default handlers used across tests. Individual tests can override these
 * with `server.use(...)` for error/edge-case scenarios (e.g. 422s, polling
 * transitions).
 */
export const handlers = [
  http.get(`${API_URL}/health`, () => {
    return HttpResponse.json({ status: 'ok' })
  }),

  http.post(`${API_URL}/jobs`, async () => {
    return HttpResponse.json(makeJob({ status: 'pending', page_count: null, card_count: 0 }), {
      status: 201,
    })
  }),

  http.get(`${API_URL}/jobs/:jobId`, () => {
    return HttpResponse.json(makeJob())
  }),

  http.get(`${API_URL}/jobs/:jobId/cards`, () => {
    return HttpResponse.json([
      makeCard(),
      makeCard({ id: 11, note_type: 'cloze', front: null, back: null, source_page: 8, cloze_text: '{{c1::Helicase}} unwinds DNA.' }),
    ])
  }),

  http.put(`${API_URL}/cards/:cardId`, async ({ params, request }) => {
    const body = (await request.json()) as Partial<CardDraft>
    return HttpResponse.json(makeCard({ id: Number(params.cardId), ...body }))
  }),

  http.delete(`${API_URL}/cards/:cardId`, () => {
    return new HttpResponse(null, { status: 204 })
  }),

  http.get(`${API_URL}/cards/:cardId/image`, () => {
    return new HttpResponse(STUB_PNG_BYTES, {
      status: 200,
      headers: { 'Content-Type': 'image/png' },
    })
  }),

  http.post(`${API_URL}/jobs/:jobId/export`, () => {
    return new HttpResponse(new Blob(['fake-apkg-bytes']), {
      status: 200,
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': 'attachment; filename="biology-lecture-3.apkg"',
      },
    })
  }),
]
