import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { API_URL } from '../test/handlers'
import { exportJob, ExportJobError, parseContentDispositionFilename } from './exportJob'

describe('parseContentDispositionFilename', () => {
  it('extracts the filename from a standard attachment header', () => {
    expect(
      parseContentDispositionFilename('attachment; filename="biology-lecture-3.apkg"'),
    ).toBe('biology-lecture-3.apkg')
  })

  it('falls back to a default filename when the header is missing', () => {
    expect(parseContentDispositionFilename(null)).toBe('deck.apkg')
  })
})

describe('exportJob', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('downloads the exported blob via a temporary anchor element', async () => {
    server.use(
      http.post(`${API_URL}/jobs/1/export`, () => {
        return new HttpResponse(new Blob(['fake-apkg-bytes']), {
          status: 200,
          headers: {
            'Content-Type': 'application/octet-stream',
            'Content-Disposition': 'attachment; filename="biology-lecture-3.apkg"',
          },
        })
      }),
    )

    // jsdom doesn't implement `URL.createObjectURL`/`revokeObjectURL`, so
    // stub them before spying (spying on a non-existent property throws).
    if (!URL.createObjectURL) {
      URL.createObjectURL = () => ''
    }
    if (!URL.revokeObjectURL) {
      URL.revokeObjectURL = () => {}
    }
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-url')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await exportJob(1)

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')

    clickSpy.mockRestore()
  })

  it('throws an ExportJobError with the server detail on failure', async () => {
    server.use(
      http.post(`${API_URL}/jobs/1/export`, () => {
        return HttpResponse.json({ detail: 'Job is not ready.' }, { status: 409 })
      }),
    )

    await expect(exportJob(1)).rejects.toThrow(ExportJobError)
    await expect(exportJob(1)).rejects.toThrow(/job is not ready/i)
  })
})
