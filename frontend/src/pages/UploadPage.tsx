import { type FormEvent, useState } from 'react'
import { Alert, Button, Container, FileInput, Stack, TextInput, Title } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useCreateJobMutation } from '../api/baseApi'
import { BackendHealth } from '../components/BackendHealth'
import { getErrorDetail } from '../api/errors'

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
const DECK_NAME_MAX_LENGTH = 100

interface FormErrors {
  deckName?: string
  file?: string
}

function validateDeckName(deckName: string): string | undefined {
  const trimmed = deckName.trim()
  if (trimmed.length === 0) {
    return 'Deck name is required.'
  }
  if (trimmed.length > DECK_NAME_MAX_LENGTH) {
    return `Deck name must be ${DECK_NAME_MAX_LENGTH} characters or fewer.`
  }
  return undefined
}

function validateFile(file: File | null): string | undefined {
  if (!file) {
    return 'A PDF file is required.'
  }
  const isPdfMime = file.type === 'application/pdf'
  const isPdfExtension = file.name.toLowerCase().endsWith('.pdf')
  if (!isPdfMime && !isPdfExtension) {
    return 'File must be a PDF.'
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return 'File must be 50 MB or smaller.'
  }
  return undefined
}

/**
 * Upload screen: deck name + PDF file, client-side validation, and job
 * creation. On success, navigates to the job's Processing/Review page.
 */
export function UploadPage() {
  const navigate = useNavigate()
  const [deckName, setDeckName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [errors, setErrors] = useState<FormErrors>({})
  const [createJob, { isLoading, error: submitError }] = useCreateJobMutation()

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    const deckNameError = validateDeckName(deckName)
    const fileError = validateFile(file)
    if (deckNameError || fileError) {
      setErrors({ deckName: deckNameError, file: fileError })
      return
    }
    setErrors({})

    try {
      const job = await createJob({ deckName: deckName.trim(), file: file as File }).unwrap()
      navigate(`/jobs/${job.id}`)
    } catch {
      // Server error is surfaced below via `submitError`.
    }
  }

  const serverErrorMessage = submitError ? getErrorDetail(submitError) : null

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Title order={1}>Study Buster</Title>
        <BackendHealth />
        <form onSubmit={handleSubmit} noValidate>
          <Stack gap="md">
            <TextInput
              label="Deck name"
              placeholder="Biology Lecture 3"
              value={deckName}
              onChange={(event) => setDeckName(event.currentTarget.value)}
              error={errors.deckName}
              maxLength={DECK_NAME_MAX_LENGTH}
              required
              disabled={isLoading}
            />
            <FileInput
              label="Lecture PDF"
              placeholder="Choose a PDF file"
              accept="application/pdf"
              value={file}
              onChange={setFile}
              error={errors.file}
              clearable
              required
              disabled={isLoading}
            />
            {serverErrorMessage && (
              <Alert color="red" title="Could not create job">
                {serverErrorMessage}
              </Alert>
            )}
            <Button type="submit" loading={isLoading} disabled={isLoading}>
              Generate
            </Button>
          </Stack>
        </form>
      </Stack>
    </Container>
  )
}
