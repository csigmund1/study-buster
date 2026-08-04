import { setupServer } from 'msw/node'
import { handlers } from './handlers'

/** MSW server used by Vitest so tests never hit a real backend. */
export const server = setupServer(...handlers)
