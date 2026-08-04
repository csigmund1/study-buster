import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react'
import type { Job } from '../types/job'
import type { CardDraft, UpdateCardRequest } from '../types/card'

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export interface HealthResponse {
  status: string
}

export interface CreateJobArgs {
  deckName: string
  file: File
}

export interface UpdateCardArgs {
  cardId: number
  body: UpdateCardRequest
}

/**
 * Single RTK Query API slice for the backend. Owns the base configuration,
 * tag types, and all job/card endpoints. Export (`POST /jobs/{id}/export`)
 * is intentionally NOT an endpoint here — it streams a binary file and is
 * handled by the `exportJob` helper in `exportJob.ts` instead.
 */
export const baseApi = createApi({
  reducerPath: 'api',
  baseQuery: fetchBaseQuery({ baseUrl: API_BASE_URL }),
  tagTypes: ['Job', 'CardDraft'],
  endpoints: (builder) => ({
    getHealth: builder.query<HealthResponse, void>({
      query: () => '/health',
    }),
    getJob: builder.query<Job, number>({
      query: (jobId) => `/jobs/${jobId}`,
      providesTags: (_result, _error, jobId) => [{ type: 'Job', id: jobId }],
    }),
    getCards: builder.query<CardDraft[], number>({
      query: (jobId) => `/jobs/${jobId}/cards`,
      providesTags: (result) =>
        result
          ? [
              ...result.map((card) => ({ type: 'CardDraft' as const, id: card.id })),
              { type: 'CardDraft' as const, id: 'LIST' },
            ]
          : [{ type: 'CardDraft' as const, id: 'LIST' }],
    }),
    createJob: builder.mutation<Job, CreateJobArgs>({
      query: ({ deckName, file }) => {
        const formData = new FormData()
        formData.append('deck_name', deckName)
        formData.append('file', file)
        return {
          url: '/jobs',
          method: 'POST',
          body: formData,
        }
      },
      invalidatesTags: ['Job'],
    }),
    updateCard: builder.mutation<CardDraft, UpdateCardArgs>({
      query: ({ cardId, body }) => ({
        url: `/cards/${cardId}`,
        method: 'PUT',
        body,
      }),
      invalidatesTags: (_result, _error, { cardId }) => [
        { type: 'CardDraft', id: cardId },
        { type: 'CardDraft', id: 'LIST' },
      ],
    }),
    deleteCard: builder.mutation<void, number>({
      query: (cardId) => ({
        url: `/cards/${cardId}`,
        method: 'DELETE',
      }),
      invalidatesTags: (_result, _error, cardId) => [
        { type: 'CardDraft', id: cardId },
        { type: 'CardDraft', id: 'LIST' },
      ],
    }),
  }),
})

export const {
  useGetHealthQuery,
  useGetJobQuery,
  useGetCardsQuery,
  useCreateJobMutation,
  useUpdateCardMutation,
  useDeleteCardMutation,
} = baseApi
