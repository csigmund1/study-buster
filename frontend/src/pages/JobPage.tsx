import { useEffect, useState } from 'react'
import { Alert, Container, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { useParams } from 'react-router-dom'
import { useGetJobQuery } from '../api/baseApi'
import { getErrorDetail } from '../api/errors'
import { ProcessingView } from '../components/ProcessingView'
import { ReviewView } from '../components/ReviewView'

const ACTIVE_STATUSES = new Set(['pending', 'processing'])
const POLL_INTERVAL_MS = 3000

/**
 * Job page: fetches the job, polls every 3s while it's `pending`/
 * `processing`, and renders Processing, an error view (on `failed`), or
 * Review (on `ready`). Polling stops as soon as the job leaves the active
 * statuses.
 */
export function JobPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const numericJobId = Number(jobId)
  const isValidJobId = jobId !== undefined && Number.isFinite(numericJobId)

  const [pollingInterval, setPollingInterval] = useState(POLL_INTERVAL_MS)

  const {
    data: job,
    isLoading,
    isError,
    error,
  } = useGetJobQuery(numericJobId, {
    skip: !isValidJobId,
    pollingInterval,
  })

  useEffect(() => {
    if (job && !ACTIVE_STATUSES.has(job.status) && pollingInterval !== 0) {
      setPollingInterval(0)
    }
  }, [job, pollingInterval])

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Title order={1}>Study Buster</Title>
        {renderBody()}
      </Stack>
    </Container>
  )

  function renderBody() {
    if (!isValidJobId) {
      return (
        <Alert color="red" title="Invalid job">
          No job ID was provided.
        </Alert>
      )
    }

    if (isLoading) {
      return (
        <Group gap="xs">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">
            Loading job…
          </Text>
        </Group>
      )
    }

    if (isError || !job) {
      return (
        <Alert color="red" title="Could not load job">
          {getErrorDetail(error)}
        </Alert>
      )
    }

    if (job.status === 'failed') {
      return (
        <Alert color="red" title="Processing failed">
          {job.error_message ?? 'An unknown error occurred while processing this deck.'}
        </Alert>
      )
    }

    if (job.status === 'ready') {
      return <ReviewView job={job} />
    }

    return <ProcessingView job={job} />
  }
}
