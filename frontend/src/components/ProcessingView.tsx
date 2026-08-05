import { Loader, Progress, Stack, Text } from '@mantine/core'
import type { Job } from '../types/job'
import { formatEta } from './formatEta'

const STATUS_LABEL: Record<'pending' | 'processing', string> = {
  pending: 'Waiting to start…',
  processing: 'Processing your PDF…',
}

/**
 * Shown while a job is `pending` or `processing`. `JobPage` polls
 * `getJob` every 2s and stops once the job leaves this range.
 *
 * Progress is server state: when `progress_percent` is `null` the stage's
 * denominator is unknown, so we show an indeterminate `Loader` rather than
 * fabricating a percentage. The same holds for `eta_seconds`.
 */
export function ProcessingView({ job }: { job: Job }) {
  const statusLabel = STATUS_LABEL[job.status as 'pending' | 'processing']
  const etaLabel = formatEta(job.eta_seconds)
  const percent = job.progress_percent

  return (
    <Stack align="center" gap="md" py="xl">
      {percent !== null ? (
        <Stack gap={4} w="100%" maw={360}>
          <Progress
            data-testid="processing-progress"
            value={percent}
            role="progressbar"
            aria-label="Generation progress"
            aria-valuenow={Math.round(percent)}
            aria-valuemin={0}
            aria-valuemax={100}
            size="lg"
            radius="sm"
          />
          <Text size="sm" c="dimmed" ta="center">
            {Math.round(percent)}%
          </Text>
        </Stack>
      ) : (
        <Loader data-testid="processing-loader" />
      )}

      <Text fw={500}>{job.stage_label ?? statusLabel}</Text>

      {etaLabel !== null && (
        <Text size="sm" c="dimmed" data-testid="processing-eta">
          {etaLabel}
        </Text>
      )}

      {job.page_count !== null && (
        <Text size="sm" c="dimmed">
          {job.page_count} page{job.page_count === 1 ? '' : 's'}
        </Text>
      )}
    </Stack>
  )
}
