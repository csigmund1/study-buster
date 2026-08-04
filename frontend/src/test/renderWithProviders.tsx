import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { configureStore } from '@reduxjs/toolkit'
import { Provider } from 'react-redux'
import { MantineProvider } from '@mantine/core'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { baseApi } from '../api/baseApi'

interface RenderWithProvidersOptions {
  /** Initial history entry, e.g. `/jobs/1`. Defaults to `/`. */
  route?: string
  /** Route pattern the component is mounted under, e.g. `/jobs/:jobId`. */
  path?: string
}

/**
 * Renders a component wrapped in a fresh Redux store (with the RTK Query
 * `baseApi` wired in), `MantineProvider`, and a `MemoryRouter`, so each test
 * gets an isolated RTK Query cache instead of sharing the app-wide store
 * singleton, and components using router hooks (`useNavigate`,
 * `useParams`) work without mounting the real `<App />`.
 */
export function renderWithProviders(ui: ReactElement, options: RenderWithProvidersOptions = {}) {
  const { route = '/', path = '/' } = options

  const store = configureStore({
    reducer: {
      [baseApi.reducerPath]: baseApi.reducer,
    },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware().concat(baseApi.middleware),
  })

  return render(
    <Provider store={store}>
      <MantineProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </MemoryRouter>
      </MantineProvider>
    </Provider>,
  )
}
