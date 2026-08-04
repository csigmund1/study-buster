import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { BackendHealth } from './BackendHealth'

describe('BackendHealth', () => {
  it('renders the ok state once the mocked /health request resolves', async () => {
    renderWithProviders(<BackendHealth />)

    expect(await screen.findByText(/backend: ok/i)).toBeInTheDocument()
  })
})
