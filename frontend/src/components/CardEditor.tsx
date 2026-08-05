import { useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Image,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  Textarea,
} from '@mantine/core'
import { API_BASE_URL, useDeleteCardMutation, useUpdateCardMutation } from '../api/baseApi'
import { getErrorDetail } from '../api/errors'
import { isOcclusionNoteType } from '../types/card'
import type { CardDraft, NoteType } from '../types/card'

interface CardEditorProps {
  jobId: number
  card: CardDraft
}

const NOTE_TYPE_BADGE: Record<NoteType, { color: string; label: string }> = {
  basic: { color: 'blue', label: 'Basic' },
  cloze: { color: 'grape', label: 'Cloze' },
  diagram: { color: 'teal', label: 'Diagram' },
  text_occlusion: { color: 'orange', label: 'Fill-in-blank' },
}

/**
 * Editable card row. Keeps its own local form state (front/back or
 * cloze_text depending on the locally-selected note type) so edits aren't
 * committed until Save. Switching note type locally reveals the fields the
 * new type needs; Save always sends `note_type` plus the fields the target
 * type requires, per the API contract.
 */
export function CardEditor({ jobId, card }: CardEditorProps) {
  const isOcclusion = isOcclusionNoteType(card.note_type)
  const [noteType, setNoteType] = useState<NoteType>(card.note_type)
  const [front, setFront] = useState(card.front ?? '')
  const [back, setBack] = useState(card.back ?? '')
  const [clozeText, setClozeText] = useState(card.cloze_text ?? '')
  const [needsPageImage, setNeedsPageImage] = useState(card.needs_page_image)
  const [imageOpen, setImageOpen] = useState(false)
  const [answerRevealed, setAnswerRevealed] = useState(false)

  const [updateCard, { isLoading: isSaving, error: saveError }] = useUpdateCardMutation()
  const [deleteCard, { isLoading: isDeleting }] = useDeleteCardMutation()

  const handleSave = async () => {
    const body = isOcclusion
      ? { note_type: card.note_type, front, back }
      : noteType === 'basic'
        ? { note_type: noteType as NoteType, front, back }
        : { note_type: noteType as NoteType, cloze_text: clozeText }

    try {
      await updateCard({ cardId: card.id, body }).unwrap()
    } catch {
      // Surfaced below via `saveError`.
    }
  }

  const handleDelete = async () => {
    const confirmed = window.confirm('Delete this card? This cannot be undone.')
    if (!confirmed) {
      return
    }
    await deleteCard(card.id)
  }

  const saveErrorMessage = saveError ? getErrorDetail(saveError) : null
  const badge = NOTE_TYPE_BADGE[noteType]

  return (
    <Card withBorder padding="md" data-testid={`card-${card.id}`}>
      <Stack gap="sm">
        <Group justify="space-between">
          <Badge color={badge.color} variant="light">
            {badge.label}
          </Badge>
          <Text size="sm" c="dimmed">
            Page {card.source_page}
          </Text>
        </Group>

        {isOcclusion ? (
          <>
            <Image
              src={`${API_BASE_URL}/cards/${card.id}/image?side=question`}
              alt={`${badge.label} question`}
              fit="contain"
              mah={300}
            />

            <Button
              variant="subtle"
              size="xs"
              onClick={() => setAnswerRevealed((revealed) => !revealed)}
              style={{ alignSelf: 'flex-start' }}
            >
              {answerRevealed ? 'Hide answer' : 'Reveal answer'}
            </Button>
            <Collapse expanded={answerRevealed}>
              <Stack gap="xs">
                <Image
                  src={`${API_BASE_URL}/cards/${card.id}/image?side=answer`}
                  alt={`${badge.label} answer`}
                  fit="contain"
                  mah={300}
                />
                <Text size="sm">{card.back}</Text>
              </Stack>
            </Collapse>

            <Textarea
              label="Front"
              value={front}
              onChange={(event) => setFront(event.currentTarget.value)}
              autosize
              minRows={2}
            />
            <Textarea
              label="Back"
              value={back}
              onChange={(event) => setBack(event.currentTarget.value)}
              autosize
              minRows={2}
            />
          </>
        ) : (
          <>
            <SegmentedControl
              value={noteType}
              onChange={(value) => setNoteType(value as NoteType)}
              data={[
                { label: 'Basic', value: 'basic' },
                { label: 'Cloze', value: 'cloze' },
              ]}
            />

            {noteType === 'basic' ? (
              <>
                <Textarea
                  label="Front"
                  value={front}
                  onChange={(event) => setFront(event.currentTarget.value)}
                  autosize
                  minRows={2}
                />
                <Textarea
                  label="Back"
                  value={back}
                  onChange={(event) => setBack(event.currentTarget.value)}
                  autosize
                  minRows={2}
                />
              </>
            ) : (
              <Textarea
                label="Cloze text"
                description="Use {{c1::…}} syntax"
                value={clozeText}
                onChange={(event) => setClozeText(event.currentTarget.value)}
                autosize
                minRows={2}
              />
            )}

            <Switch
              label="Include page image at export"
              checked={needsPageImage}
              onChange={(event) => setNeedsPageImage(event.currentTarget.checked)}
            />

            <Button
              variant="subtle"
              size="xs"
              onClick={() => setImageOpen((open) => !open)}
              style={{ alignSelf: 'flex-start' }}
            >
              {imageOpen ? 'Hide page preview' : 'Show page preview'}
            </Button>
            <Collapse expanded={imageOpen}>
              <Image
                src={`${API_BASE_URL}/jobs/${jobId}/pages/${card.source_page}`}
                alt={`Page ${card.source_page} preview`}
                fit="contain"
                mah={300}
              />
            </Collapse>
          </>
        )}

        {saveErrorMessage && (
          <Alert color="red" title="Could not save card">
            {saveErrorMessage}
          </Alert>
        )}

        <Group justify="flex-end">
          <Button color="red" variant="outline" onClick={handleDelete} loading={isDeleting}>
            Delete
          </Button>
          <Button onClick={handleSave} loading={isSaving}>
            Save
          </Button>
        </Group>
      </Stack>
    </Card>
  )
}
