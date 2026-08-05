import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { makeJob } from '../test/handlers'
import { ProcessingView } from './ProcessingView'
import { formatEta } from './formatEta'

function processingJob(overrides: Parameters<typeof makeJob>[0] = {}) {
  return makeJob({
    status: 'processing',
    card_count: 0,
    stage: 'generating_cards',
    stage_label: 'Generating cards',
    progress_percent: null,
    eta_seconds: null,
    ...overrides,
  })
}

describe('ProcessingView', () => {
  it('renders a determinate bar with the percentage and the ETA when both are present', () => {
    renderWithProviders(
      <ProcessingView job={processingJob({ progress_percent: 42.4, eta_seconds: 137 })} />,
    )

    const bar = screen.getByTestId('processing-progress')
    expect(bar).toBeInTheDocument()
    expect(bar).toHaveAttribute('aria-valuenow', '42')
    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('about 2 min remaining')).toBeInTheDocument()
    expect(screen.queryByTestId('processing-loader')).not.toBeInTheDocument()
  })

  it('shows the indeterminate loader and no percentage when progress_percent is null', () => {
    renderWithProviders(
      <ProcessingView job={processingJob({ progress_percent: null, eta_seconds: null })} />,
    )

    expect(screen.getByTestId('processing-loader')).toBeInTheDocument()
    expect(screen.queryByTestId('processing-progress')).not.toBeInTheDocument()
    // The honesty requirement: an unknown percentage is absent, never 0.
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('renders the bar without ETA text when eta_seconds is null', () => {
    renderWithProviders(
      <ProcessingView job={processingJob({ progress_percent: 12.5, eta_seconds: null })} />,
    )

    expect(screen.getByTestId('processing-progress')).toBeInTheDocument()
    expect(screen.getByText('13%')).toBeInTheDocument()
    expect(screen.queryByTestId('processing-eta')).not.toBeInTheDocument()
    expect(screen.queryByText(/remaining/i)).not.toBeInTheDocument()
  })

  it('shows the stage label instead of the generic status text when present', () => {
    renderWithProviders(
      <ProcessingView job={processingJob({ stage: 'rendering', stage_label: 'Rendering pages' })} />,
    )

    expect(screen.getByText('Rendering pages')).toBeInTheDocument()
    expect(screen.queryByText(/processing your pdf/i)).not.toBeInTheDocument()
  })

  it('falls back to the status text when there is no stage label', () => {
    renderWithProviders(
      <ProcessingView
        job={processingJob({ status: 'pending', stage: null, stage_label: null, page_count: null })}
      />,
    )

    expect(screen.getByText(/waiting to start/i)).toBeInTheDocument()
    expect(screen.getByTestId('processing-loader')).toBeInTheDocument()
  })

  it('keeps showing the page count', () => {
    renderWithProviders(<ProcessingView job={processingJob({ page_count: 1 })} />)

    expect(screen.getByText('1 page')).toBeInTheDocument()
  })
})

describe('formatEta', () => {
  it('returns null when there is no honest ETA', () => {
    expect(formatEta(null)).toBeNull()
    expect(formatEta(0)).toBeNull()
    expect(formatEta(-5)).toBeNull()
  })

  it('collapses sub-minute values to a coarse phrase', () => {
    expect(formatEta(1)).toBe('less than a minute remaining')
    expect(formatEta(59)).toBe('less than a minute remaining')
  })

  it('rounds to whole minutes', () => {
    expect(formatEta(60)).toBe('about 1 min remaining')
    expect(formatEta(89)).toBe('about 1 min remaining')
    expect(formatEta(90)).toBe('about 2 min remaining')
    expect(formatEta(137)).toBe('about 2 min remaining')
    expect(formatEta(600)).toBe('about 10 min remaining')
    expect(formatEta(3540)).toBe('about 59 min remaining')
  })

  it('switches to hours past an hour', () => {
    expect(formatEta(3600)).toBe('about 1 hr remaining')
    expect(formatEta(7200)).toBe('about 2 hr remaining')
  })
})
