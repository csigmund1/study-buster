import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import { API_URL, makeJob } from '../test/handlers'
import { UploadPage } from './UploadPage'

function JobRouteStub() {
  const { jobId } = useParams<{ jobId: string }>()
  return <div>Job page for {jobId}</div>
}

/**
 * Renders `UploadPage` alongside a stub `/jobs/:jobId` route so navigation
 * on successful submit can be asserted end-to-end.
 */
function renderUploadPage() {
  return renderWithProviders(<UploadPage />, {
    extraRoutes: [{ path: '/jobs/:jobId', element: <JobRouteStub /> }],
  })
}

function pdfFile(name = 'lecture.pdf', size = 1024) {
  const file = new File([new Uint8Array(size)], name, { type: 'application/pdf' })
  return file
}

/**
 * Mantine's `FileInput` renders a visual `<button>` associated with the
 * label and a separate, unlabeled hidden `<input type="file">` that
 * actually accepts uploads, so `getByLabelText` can't find it directly.
 */
function getFileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')
  if (!input) {
    throw new Error('Expected a hidden file input to be present.')
  }
  return input
}

describe('UploadPage', () => {
  it('shows validation errors when submitted empty', async () => {
    const user = userEvent.setup()
    renderUploadPage()

    await user.click(screen.getByRole('button', { name: /generate/i }))

    expect(await screen.findByText(/deck name is required/i)).toBeInTheDocument()
    expect(screen.getByText(/pdf file is required/i)).toBeInTheDocument()
  })

  it('rejects a non-PDF file', async () => {
    // `applyAccept: false` bypasses userEvent's OS-picker-style filtering by
    // the input's `accept` attribute, simulating a file arriving via
    // drag-and-drop (which browsers don't filter) so the app's own
    // client-side validation is exercised.
    const user = userEvent.setup({ applyAccept: false })
    const { container } = renderUploadPage()

    await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
    const textFile = new File(['not a pdf'], 'notes.txt', { type: 'text/plain' })
    await user.upload(getFileInput(container), textFile)
    await user.click(screen.getByRole('button', { name: /generate/i }))

    expect(await screen.findByText(/file must be a pdf/i)).toBeInTheDocument()
  })

  it('rejects an oversized file', async () => {
    const user = userEvent.setup()
    const { container } = renderUploadPage()

    await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
    const oversized = pdfFile('big.pdf', 51 * 1024 * 1024)
    await user.upload(getFileInput(container), oversized)
    await user.click(screen.getByRole('button', { name: /generate/i }))

    expect(await screen.findByText(/50 mb or smaller/i)).toBeInTheDocument()
  })

  it('submits successfully and navigates to the new job page', async () => {
    const user = userEvent.setup()
    const { container } = renderUploadPage()

    await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
    await user.upload(getFileInput(container), pdfFile())
    await user.click(screen.getByRole('button', { name: /generate/i }))

    expect(await screen.findByText(/job page for 1/i)).toBeInTheDocument()
  })

  it('surfaces a server-provided error detail on failure', async () => {
    server.use(
      http.post(`${API_URL}/jobs`, () => {
        return HttpResponse.json({ detail: 'Deck name already in use.' }, { status: 422 })
      }),
    )

    const user = userEvent.setup()
    const { container } = renderUploadPage()

    await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
    await user.upload(getFileInput(container), pdfFile())
    await user.click(screen.getByRole('button', { name: /generate/i }))

    expect(await screen.findByText(/deck name already in use/i)).toBeInTheDocument()
  })

  it('disables the submit button while the request is in flight', async () => {
    server.use(
      http.post(`${API_URL}/jobs`, async () => {
        await new Promise((resolve) => setTimeout(resolve, 50))
        return HttpResponse.json(makeJob({ status: 'pending' }), { status: 201 })
      }),
    )

    const user = userEvent.setup()
    const { container } = renderUploadPage()

    await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
    await user.upload(getFileInput(container), pdfFile())
    await user.click(screen.getByRole('button', { name: /generate/i }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /generate/i })).toBeDisabled()
    })
  })
})
