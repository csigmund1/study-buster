import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import { API_URL, makeJob } from '../test/handlers'
import { JobPage } from './JobPage'

function renderJobPage(jobId = '1') {
  return renderWithProviders(<JobPage />, { route: `/jobs/${jobId}`, path: '/jobs/:jobId' })
}

describe('JobPage', () => {
  it('shows the Processing view while the job is processing, then Review once ready', async () => {
    let callCount = 0
    server.use(
      http.get(`${API_URL}/jobs/:jobId`, () => {
        callCount += 1
        return HttpResponse.json(makeJob({ status: callCount === 1 ? 'processing' : 'ready' }))
      }),
    )

    renderJobPage()

    expect(await screen.findByText(/processing your pdf/i)).toBeInTheDocument()

    expect(
      await screen.findByText(/card/i, undefined, { timeout: 4500 }),
    ).toBeInTheDocument()
  }, 6000)

  it('shows the failed job error message', async () => {
    server.use(
      http.get(`${API_URL}/jobs/:jobId`, () => {
        return HttpResponse.json(
          makeJob({ status: 'failed', error_message: 'PDF exceeded 100 pages.' }),
        )
      }),
    )

    renderJobPage()

    expect(await screen.findByText(/pdf exceeded 100 pages/i)).toBeInTheDocument()
  })

  it('renders the Review view with cards when the job is ready', async () => {
    renderJobPage()

    expect(await screen.findByText(/2 cards/i)).toBeInTheDocument()
    expect(screen.getByText(/what enzyme unwinds dna/i)).toBeInTheDocument()
  })

  it('shows an error state for an unknown job', async () => {
    server.use(
      http.get(`${API_URL}/jobs/:jobId`, () => {
        return HttpResponse.json({ detail: 'Job not found.' }, { status: 404 })
      }),
    )

    renderJobPage()

    expect(await screen.findByText(/job not found/i)).toBeInTheDocument()
  })
})
