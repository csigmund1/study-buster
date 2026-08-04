import { useState } from 'react'
import { Alert, Button, Group, Loader, Stack, Text } from '@mantine/core'
import { useGetCardsQuery } from '../api/baseApi'
import { exportJob } from '../api/exportJob'
import { CardEditor } from './CardEditor'
import type { Job } from '../types/job'

/**
 * Review screen: fetches the job's cards and renders an editable list, plus
 * an Export button that downloads the generated `.apkg`.
 */
export function ReviewView({ job }: { job: Job }) {
  const { data: cards, isLoading, isError } = useGetCardsQuery(job.id)
  const [isExporting, setIsExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const handleExport = async () => {
    setExportError(null)
    setIsExporting(true)
    try {
      await exportJob(job.id)
    } catch (error) {
      setExportError(error instanceof Error ? error.message : 'Export failed.')
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={600} size="lg">
          {cards ? `${cards.length} card${cards.length === 1 ? '' : 's'}` : 'Cards'}
        </Text>
        <Button onClick={handleExport} loading={isExporting} disabled={job.status !== 'ready'}>
          Export
        </Button>
      </Group>

      {exportError && (
        <Alert color="red" title="Export failed">
          {exportError}
        </Alert>
      )}

      {isLoading && (
        <Group gap="xs">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">
            Loading cards…
          </Text>
        </Group>
      )}

      {isError && (
        <Alert color="red" title="Could not load cards">
          Please refresh the page to try again.
        </Alert>
      )}

      {!isLoading && !isError && cards?.length === 0 && (
        <Text c="dimmed">No cards were generated for this deck.</Text>
      )}

      {cards?.map((card) => <CardEditor key={card.id} jobId={job.id} card={card} />)}
    </Stack>
  )
}
