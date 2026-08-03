import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import { API_URL, makeCard, makeOcclusion } from '../test/handlers'
import { CardEditor } from './CardEditor'

describe('CardEditor', () => {
  it('saves edited front/back text for a basic card', async () => {
    let receivedBody: unknown = null
    server.use(
      http.put(`${API_URL}/cards/:cardId`, async ({ request }) => {
        receivedBody = await request.json()
        return HttpResponse.json(makeCard({ front: 'Updated front' }))
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={makeCard()} />)

    const frontInput = screen.getByLabelText(/front/i)
    await user.clear(frontInput)
    await user.type(frontInput, 'Updated front')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await screen.findByRole('button', { name: /save/i })
    expect(receivedBody).toMatchObject({ note_type: 'basic', front: 'Updated front' })
  })

  it('shows a 422 detail inline when save fails', async () => {
    server.use(
      http.put(`${API_URL}/cards/:cardId`, () => {
        return HttpResponse.json({ detail: 'Cloze text must contain {{c1::...}}.' }, { status: 422 })
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={makeCard()} />)

    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/cloze text must contain/i)).toBeInTheDocument()
  })

  it('deletes a card after confirmation', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    let deleteCalled = false
    server.use(
      http.delete(`${API_URL}/cards/:cardId`, () => {
        deleteCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={makeCard()} />)

    await user.click(screen.getByRole('button', { name: /delete/i }))

    expect(confirmSpy).toHaveBeenCalled()
    await vi.waitFor(() => expect(deleteCalled).toBe(true))

    confirmSpy.mockRestore()
  })

  it('does not delete when the confirmation is dismissed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    let deleteCalled = false
    server.use(
      http.delete(`${API_URL}/cards/:cardId`, () => {
        deleteCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={makeCard()} />)

    await user.click(screen.getByRole('button', { name: /delete/i }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(deleteCalled).toBe(false)

    confirmSpy.mockRestore()
  })

  it('switching to cloze reveals the cloze text field and includes it on save', async () => {
    let receivedBody: unknown = null
    server.use(
      http.put(`${API_URL}/cards/:cardId`, async ({ request }) => {
        receivedBody = await request.json()
        return HttpResponse.json(makeCard({ note_type: 'cloze', cloze_text: '{{c1::Helicase}}' }))
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={makeCard()} />)

    await user.click(screen.getByRole('radio', { name: /cloze/i }))
    const clozeInput = screen.getByLabelText(/cloze text/i)
    // userEvent's keyboard syntax treats "{" as special, so "{{" escapes to
    // a single literal "{"; "}" needs no escaping.
    await user.type(clozeInput, '{{{{c1::Helicase}}')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await vi.waitFor(() =>
      expect(receivedBody).toMatchObject({ note_type: 'cloze', cloze_text: '{{c1::Helicase}}' }),
    )
  })

  it('renders a diagram card with question image, no note-type control, and reveals the answer', async () => {
    let receivedBody: unknown = null
    server.use(
      http.put(`${API_URL}/cards/:cardId`, async ({ request }) => {
        receivedBody = await request.json()
        return HttpResponse.json(
          makeCard({
            note_type: 'diagram',
            front: 'What structure is this?',
            back: 'Mitochondrion',
            occlusion: makeOcclusion(),
          }),
        )
      }),
    )

    const diagramCard = makeCard({
      id: 20,
      note_type: 'diagram',
      front: 'What structure is this?',
      back: 'Helicase',
      occlusion: makeOcclusion(),
    })

    const user = userEvent.setup()
    renderWithProviders(<CardEditor jobId={1} card={diagramCard} />)

    expect(screen.getByText('Diagram')).toBeInTheDocument()
    expect(
      screen.queryByRole('radio', { name: /basic/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('radio', { name: /cloze/i }),
    ).not.toBeInTheDocument()

    const questionImage = screen.getByAltText('Diagram question')
    expect(questionImage).toHaveAttribute(
      'src',
      `${API_URL}/cards/20/image?side=question`,
    )

    expect(screen.getByRole('button', { name: /reveal answer/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /reveal answer/i }))

    const answerImage = await screen.findByAltText('Diagram answer')
    expect(answerImage).toHaveAttribute(
      'src',
      `${API_URL}/cards/20/image?side=answer`,
    )
    expect(screen.getAllByText('Helicase').length).toBeGreaterThan(0)

    const frontInput = screen.getByLabelText(/front/i)
    await user.clear(frontInput)
    await user.type(frontInput, 'What structure is this?')
    const backInput = screen.getByLabelText(/back/i)
    await user.clear(backInput)
    await user.type(backInput, 'Mitochondrion')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await vi.waitFor(() =>
      expect(receivedBody).toMatchObject({
        note_type: 'diagram',
        front: 'What structure is this?',
        back: 'Mitochondrion',
      }),
    )
  })
})
