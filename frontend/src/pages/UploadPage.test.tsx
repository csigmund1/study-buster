import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useParams } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { GenerationOptions } from '../types/generationOptions'
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

/**
 * Installs a `POST /jobs` handler that captures the submitted `options` form
 * field, so assertions run against the real intercepted request body rather
 * than a mocked client function.
 */
function captureSubmittedOptions(): { current: GenerationOptions | null } {
  const captured: { current: GenerationOptions | null } = { current: null }
  server.use(
    http.post(`${API_URL}/jobs`, async ({ request }) => {
      captured.current = parseOptionsPart(await request.text())
      return HttpResponse.json(makeJob({ status: 'pending' }), { status: 201 })
    }),
  )
  return captured
}

/**
 * Pulls the `options` part out of a raw multipart body. `request.formData()`
 * can't be used here: undici refuses to parse a multipart body assembled from
 * jsdom's `File`, so the raw payload is read instead.
 */
function parseOptionsPart(body: string): GenerationOptions | null {
  const match = /name="options"\r?\n\r?\n([\s\S]*?)\r?\n--/.exec(body)
  return match ? (JSON.parse(match[1]) as GenerationOptions) : null
}

describe('UploadPage', () => {
  // The generation options persist in localStorage by design, so each case
  // must start from a clean slate rather than inherit the previous one.
  beforeEach(() => {
    window.localStorage.clear()
  })

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

  describe('generation options', () => {
    /** Scopes a radio query to one `SegmentedControl`; the two grouping
     * controls share option labels, so an unscoped query is ambiguous. */
    const option = (group: string, label: string) =>
      within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name: label })

    const diagramSwitch = () => screen.getByLabelText(/generate diagram cards/i)

    it('renders the four controls with the documented defaults', () => {
      renderUploadPage()

      expect(screen.getByRole('group', { name: /generation options/i })).toBeInTheDocument()
      expect(option('Card style', 'Basic & cloze')).toBeChecked()
      expect(diagramSwitch()).toBeChecked()
      expect(option('Text mask grouping', 'Individual cards')).toBeChecked()
      expect(option('Diagram mask grouping', 'Individual cards')).toBeChecked()
    })

    it('restores the last-used options after a remount', async () => {
      const user = userEvent.setup()
      const { unmount } = renderUploadPage()

      await user.click(option('Card style', 'Text occlusion'))
      await user.click(option('Text mask grouping', 'One card per page'))
      unmount()

      renderUploadPage()

      expect(option('Card style', 'Text occlusion')).toBeChecked()
      expect(option('Text mask grouping', 'One card per page')).toBeChecked()
      expect(option('Diagram mask grouping', 'Individual cards')).toBeChecked()
    })

    it('sends each kind’s grouping independently', async () => {
      const captured = captureSubmittedOptions()
      const user = userEvent.setup()
      const { container } = renderUploadPage()

      // Diagrams individual, text occlusion all on one card.
      await user.click(option('Card style', 'Text occlusion'))
      await user.click(option('Text mask grouping', 'One card per page'))
      await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
      await user.upload(getFileInput(container), pdfFile())
      await user.click(screen.getByRole('button', { name: /generate/i }))

      await waitFor(() => {
        expect(captured.current).toEqual({
          text_card_mode: 'text_occlusion',
          diagram_occlusion_enabled: true,
          diagram_mask_grouping: 'individual',
          text_mask_grouping: 'grouped',
        })
      })
    })

    it('sends the defaults when no control is touched', async () => {
      const captured = captureSubmittedOptions()
      const user = userEvent.setup()
      const { container } = renderUploadPage()

      await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
      await user.upload(getFileInput(container), pdfFile())
      await user.click(screen.getByRole('button', { name: /generate/i }))

      await waitFor(() => {
        expect(captured.current).toEqual({
          text_card_mode: 'basic_cloze',
          diagram_occlusion_enabled: true,
          diagram_mask_grouping: 'individual',
          text_mask_grouping: 'individual',
        })
      })
    })

    it('disables each grouping control only when its own kind is inert', async () => {
      const user = userEvent.setup()
      renderUploadPage()

      const textGrouping = () => option('Text mask grouping', 'Individual cards')
      const diagramGrouping = () => option('Diagram mask grouping', 'Individual cards')

      // Default: basic/cloze style, diagram cards on.
      expect(textGrouping()).toBeDisabled()
      expect(diagramGrouping()).toBeEnabled()

      // Text occlusion enables text grouping and leaves diagram grouping alone.
      await user.click(option('Card style', 'Text occlusion'))
      expect(textGrouping()).toBeEnabled()
      expect(diagramGrouping()).toBeEnabled()

      // Turning diagram cards off disables only the diagram grouping.
      await user.click(diagramSwitch())
      expect(textGrouping()).toBeEnabled()
      expect(diagramGrouping()).toBeDisabled()

      // Back to basic/cloze with diagrams still off: both are inert.
      await user.click(option('Card style', 'Basic & cloze'))
      expect(textGrouping()).toBeDisabled()
      expect(diagramGrouping()).toBeDisabled()
    })

    it('upgrades a pre-split value persisted by an older build', async () => {
      const captured = captureSubmittedOptions()
      // The previous shape: one `mask_grouping` governing both kinds.
      window.localStorage.setItem(
        'study-buster:generation-options',
        JSON.stringify({
          text_card_mode: 'text_occlusion',
          diagram_occlusion_enabled: true,
          mask_grouping: 'grouped',
        }),
      )

      const user = userEvent.setup()
      const { container } = renderUploadPage()

      expect(option('Text mask grouping', 'One card per page')).toBeChecked()
      expect(option('Diagram mask grouping', 'One card per page')).toBeChecked()

      await user.type(screen.getByLabelText(/deck name/i), 'Biology Lecture 3')
      await user.upload(getFileInput(container), pdfFile())
      await user.click(screen.getByRole('button', { name: /generate/i }))

      await waitFor(() => {
        expect(captured.current).toEqual({
          text_card_mode: 'text_occlusion',
          diagram_occlusion_enabled: true,
          diagram_mask_grouping: 'grouped',
          text_mask_grouping: 'grouped',
        })
      })
    })
  })
})
