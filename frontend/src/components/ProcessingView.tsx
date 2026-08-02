import { Loader, Stack, Text } from '@mantine/core'
import type { Job } from '../types/job'

const STATUS_LABEL: Record<'pending' | 'processing', string> = {
  pending: 'Waiting to start…',
  processing: 'Processing your PDF…',
}

/**
 * Shown while a job is `pending` or `processing`. `JobPage` polls
 * `getJob` every 3s and stops once the job leaves this range.
 */
export function ProcessingView({ job }: { job: Job }) {
  return (
    <Stack align="center" gap="md" py="xl">
      <Loader data-testid="processing-loader" />
      <Text fw={500}>{STATUS_LABEL[job.status as 'pending' | 'processing']}</Text>
      {job.page_count !== null && (
        <Text size="sm" c="dimmed">
          {job.page_count} page{job.page_count === 1 ? '' : 's'}
        </Text>
      )}
    </Stack>
  )
}
