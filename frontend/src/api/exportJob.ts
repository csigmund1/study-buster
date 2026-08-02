import { API_BASE_URL } from './baseApi'

const DEFAULT_FILENAME = 'deck.apkg'

/**
 * Parses the filename out of a `Content-Disposition: attachment;
 * filename="…"` header value. Falls back to `DEFAULT_FILENAME` when the
 * header is missing or unparsable.
 */
export function parseContentDispositionFilename(header: string | null): string {
  if (!header) {
    return DEFAULT_FILENAME
  }

  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header)
  return match?.[1] ?? DEFAULT_FILENAME
}

export class ExportJobError extends Error {}

/**
 * Triggers `POST /jobs/{jobId}/export`, reads the returned `.apkg` blob and
 * its filename from `Content-Disposition`, and prompts a browser download.
 * Not an RTK Query endpoint because it streams a binary file rather than
 * JSON — a plain `fetch` is simpler and avoids teaching RTK Query about
 * blob responses.
 */
export async function exportJob(jobId: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/export`, {
    method: 'POST',
  })

  if (!response.ok) {
    let detail = `Export failed (${response.status})`
    try {
      const body: unknown = await response.json()
      if (
        body !== null &&
        typeof body === 'object' &&
        'detail' in body &&
        typeof (body as { detail: unknown }).detail === 'string'
      ) {
        detail = (body as { detail: string }).detail
      }
    } catch {
      // Response body wasn't JSON; keep the generic message.
    }
    throw new ExportJobError(detail)
  }

  const blob = await response.blob()
  const filename = parseContentDispositionFilename(response.headers.get('Content-Disposition'))

  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
