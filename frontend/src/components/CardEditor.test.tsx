import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import { API_URL, makeCard, makeOcclusion, makeTextOcclusion } from '../test/handlers'
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

  it('renders a text-occlusion card with composed images and no note-type control', async () => {
    const user = userEvent.setup()
    const textCard = makeCard({
      id: 30,
      note_type: 'text_occlusion',
      front: 'Fill in the blank',
      back: 'unwinds the double helix',
      occlusion: makeTextOcclusion(),
    })

    renderWithProviders(<CardEditor jobId={1} card={textCard} />)

    expect(screen.getByText('Fill-in-blank')).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /basic/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /cloze/i })).not.toBeInTheDocument()

    expect(screen.getByAltText('Fill-in-blank question')).toHaveAttribute(
      'src',
      `${API_URL}/cards/30/image?side=question`,
    )

    await user.click(screen.getByRole('button', { name: /reveal answer/i }))

    expect(await screen.findByAltText('Fill-in-blank answer')).toHaveAttribute(
      'src',
      `${API_URL}/cards/30/image?side=answer`,
    )

    expect(screen.getByLabelText(/front/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/back/i)).toBeInTheDocument()
  })

  it('saves a text-occlusion card with its own note type', async () => {
    let receivedBody: unknown = null
    server.use(
      http.put(`${API_URL}/cards/:cardId`, async ({ request }) => {
        receivedBody = await request.json()
        return HttpResponse.json(
          makeCard({
            id: 30,
            note_type: 'text_occlusion',
            front: 'Fill in the blank',
            back: 'unwinds the double helix',
            occlusion: makeTextOcclusion(),
          }),
        )
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(
      <CardEditor
        jobId={1}
        card={makeCard({
          id: 30,
          note_type: 'text_occlusion',
          front: 'Fill in the blank',
          back: 'old answer',
          occlusion: makeTextOcclusion(),
        })}
      />,
    )

    const backInput = screen.getByLabelText(/back/i)
    await user.clear(backInput)
    await user.type(backInput, 'unwinds the double helix')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await vi.waitFor(() =>
      expect(receivedBody).toMatchObject({
        note_type: 'text_occlusion',
        front: 'Fill in the blank',
        back: 'unwinds the double helix',
      }),
    )
  })

  it('renders an occlusion card whose single label spans several target boxes', () => {
    const multiBox = makeTextOcclusion({
      labels: ['unwinds the double helix'],
      target_boxes: [
        { left: 0.3, top: 0.4, width: 0.4, height: 0.03 },
        { left: 0.1, top: 0.44, width: 0.2, height: 0.03 },
        { left: 0.1, top: 0.48, width: 0.12, height: 0.03 },
      ],
    })
    expect(multiBox.labels).toHaveLength(1)
    expect(multiBox.target_boxes).toHaveLength(3)

    renderWithProviders(
      <CardEditor
        jobId={1}
        card={makeCard({ id: 31, note_type: 'text_occlusion', occlusion: multiBox })}
      />,
    )

    expect(screen.getByAltText('Fill-in-blank question')).toBeInTheDocument()
    expect(screen.getByTestId('card-31')).toBeInTheDocument()
  })
})
