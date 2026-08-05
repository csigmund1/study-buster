import { type FormEvent, useState } from 'react'
import {
  Alert,
  Button,
  Container,
  Fieldset,
  FileInput,
  Input,
  SegmentedControl,
  Stack,
  Switch,
  TextInput,
  Title,
} from '@mantine/core'
import { useLocalStorage } from '@mantine/hooks'
import { useNavigate } from 'react-router-dom'
import { useCreateJobMutation } from '../api/baseApi'
import { BackendHealth } from '../components/BackendHealth'
import { getErrorDetail } from '../api/errors'
import {
  DEFAULT_GENERATION_OPTIONS,
  type GenerationOptions,
  type MaskGrouping,
  type TextCardMode,
  normalizeGenerationOptions,
} from '../types/generationOptions'

const GROUPING_DATA = [
  { value: 'individual', label: 'Individual cards' },
  { value: 'grouped', label: 'One card per page' },
]

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
/** Where the user's last-used generation options are remembered. */
const GENERATION_OPTIONS_STORAGE_KEY = 'study-buster:generation-options'
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
  const [storedOptions, setStoredOptions] = useLocalStorage<GenerationOptions>({
    key: GENERATION_OPTIONS_STORAGE_KEY,
    defaultValue: DEFAULT_GENERATION_OPTIONS,
    getInitialValueInEffect: false,
  })
  const [createJob, { isLoading, error: submitError }] = useCreateJobMutation()

  // A value persisted by an older build may still carry the pre-split
  // `mask_grouping`, so never read the stored object directly.
  const options = normalizeGenerationOptions(storedOptions)
  const updateOptions = (patch: Partial<GenerationOptions>) => {
    setStoredOptions((current) => ({ ...normalizeGenerationOptions(current), ...patch }))
  }

  // Each grouping control only matters when its own kind can produce masks.
  const textGroupingIsInert = options.text_card_mode !== 'text_occlusion'
  const diagramGroupingIsInert = !options.diagram_occlusion_enabled

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
      const job = await createJob({
        deckName: deckName.trim(),
        file: file as File,
        options,
      }).unwrap()
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
            <Fieldset legend="Generation options" disabled={isLoading}>
              <Stack gap="md">
                <Input.Wrapper
                  label="Card style"
                  labelElement="div"
                  description="How text on each slide becomes cards."
                >
                  <SegmentedControl
                    fullWidth
                    mt={4}
                    aria-label="Card style"
                    value={options.text_card_mode}
                    onChange={(value) =>
                      updateOptions({ text_card_mode: value as TextCardMode })
                    }
                    disabled={isLoading}
                    data={[
                      { value: 'basic_cloze', label: 'Basic & cloze' },
                      { value: 'text_occlusion', label: 'Text occlusion' },
                    ]}
                  />
                </Input.Wrapper>
                <Input.Wrapper
                  label="Text mask grouping"
                  labelElement="div"
                  description={
                    textGroupingIsInert
                      ? 'No effect right now: the card style is not text occlusion, so no text masks are produced.'
                      : 'How masked phrases become cards.'
                  }
                >
                  <SegmentedControl
                    fullWidth
                    mt={4}
                    aria-label="Text mask grouping"
                    value={options.text_mask_grouping}
                    onChange={(value) =>
                      updateOptions({ text_mask_grouping: value as MaskGrouping })
                    }
                    disabled={isLoading || textGroupingIsInert}
                    data={GROUPING_DATA}
                  />
                </Input.Wrapper>
                <Switch
                  label="Generate diagram cards"
                  description="Detect labeled diagrams and mask their labels."
                  checked={options.diagram_occlusion_enabled}
                  onChange={(event) =>
                    updateOptions({ diagram_occlusion_enabled: event.currentTarget.checked })
                  }
                  disabled={isLoading}
                />
                <Input.Wrapper
                  label="Diagram mask grouping"
                  labelElement="div"
                  description={
                    diagramGroupingIsInert
                      ? 'No effect right now: diagram cards are turned off, so no diagram masks are produced.'
                      : 'How diagram labels become cards.'
                  }
                >
                  <SegmentedControl
                    fullWidth
                    mt={4}
                    aria-label="Diagram mask grouping"
                    value={options.diagram_mask_grouping}
                    onChange={(value) =>
                      updateOptions({ diagram_mask_grouping: value as MaskGrouping })
                    }
                    disabled={isLoading || diagramGroupingIsInert}
                    data={GROUPING_DATA}
                  />
                </Input.Wrapper>
              </Stack>
            </Fieldset>
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
