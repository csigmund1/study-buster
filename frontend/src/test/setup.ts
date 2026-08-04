import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './server'

// jsdom does not implement matchMedia; Mantine's color-scheme detection
// needs it, so provide a minimal stub for tests.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

// jsdom does not implement ResizeObserver; Mantine's autosize Textarea (used
// in the Review card editor) needs it, so provide a minimal stub for tests.
if (!window.ResizeObserver) {
  window.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom does not implement `document.fonts` (the CSS Font Loading API);
// Mantine's autosize Textarea listens for font-loading events, so provide a
// minimal stub for tests.
if (!document.fonts) {
  Object.defineProperty(document, 'fonts', {
    value: {
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    configurable: true,
  })
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))

// `@testing-library/react` only auto-registers its `afterEach(cleanup)` hook
// when it detects a global `afterEach` (i.e. `test.globals: true` in the
// Vitest config). This project imports test globals explicitly instead, so
// cleanup must be wired up manually to avoid DOM/state leaking across tests
// within the same file.
afterEach(() => {
  cleanup()
  server.resetHandlers()
})

afterAll(() => server.close())
