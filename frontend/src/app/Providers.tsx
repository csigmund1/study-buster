import type { ReactNode } from 'react'
import { MantineProvider } from '@mantine/core'
import { Provider as ReduxProvider } from 'react-redux'
import { BrowserRouter } from 'react-router-dom'
import { store } from './store'

interface ProvidersProps {
  children: ReactNode
}

/**
 * Wires up the app-wide providers: Redux store, RTK Query (via the store),
 * Mantine theming, and client-side routing. Kept separate from `main.tsx` so
 * tests can wrap components without also mounting `<App />`.
 */
export function Providers({ children }: ProvidersProps) {
  return (
    <ReduxProvider store={store}>
      <MantineProvider>
        <BrowserRouter>{children}</BrowserRouter>
      </MantineProvider>
    </ReduxProvider>
  )
}
