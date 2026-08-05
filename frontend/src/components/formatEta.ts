/**
 * Formats a server-supplied ETA coarsely, with no false precision. Returns
 * `null` when there is nothing honest to show. Never estimates: the value is
 * always the backend's `eta_seconds`, which the server omits unless it can be
 * computed honestly.
 */
export function formatEta(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) {
    return null
  }
  if (seconds < 60) {
    return 'less than a minute remaining'
  }
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) {
    return `about ${minutes} min remaining`
  }
  const hours = Math.round(seconds / 3600)
  return `about ${hours} hr remaining`
}
