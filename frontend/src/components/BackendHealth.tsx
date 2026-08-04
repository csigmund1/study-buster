import { Badge, Group, Loader, Text } from '@mantine/core'
import { useGetHealthQuery } from '../api/baseApi'

/**
 * Small status readout used on the Upload page to prove the frontend can
 * reach the backend (M1 exit criterion). Shows a loading, ok, or
 * unreachable state depending on the `/health` request.
 */
export function BackendHealth() {
  const { data, isLoading, isError } = useGetHealthQuery()

  if (isLoading) {
    return (
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="sm" c="dimmed">
          Checking backend…
        </Text>
      </Group>
    )
  }

  const isOk = !isError && data?.status === 'ok'

  return (
    <Group gap="xs">
      <Badge color={isOk ? 'green' : 'red'} variant="light">
        Backend: {isOk ? 'ok' : 'unreachable'}
      </Badge>
    </Group>
  )
}
