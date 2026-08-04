import type { FetchBaseQueryError } from '@reduxjs/toolkit/query/react'
import type { SerializedError } from '@reduxjs/toolkit'

const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.'

/**
 * Extracts a human-readable message from an RTK Query error. Per the API
 * contract, the backend's error shape is `{ detail: string }` for normal
 * errors; 422s use FastAPI's standard `detail` array instead, in which case
 * we fall back to a generic message.
 */
export function getErrorDetail(error: FetchBaseQueryError | SerializedError | undefined): string {
  if (!error) {
    return GENERIC_ERROR_MESSAGE
  }

  if ('status' in error) {
    const data: unknown = error.data
    if (data !== null && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') {
        return detail
      }
    }
    return GENERIC_ERROR_MESSAGE
  }

  return error.message ?? GENERIC_ERROR_MESSAGE
}
