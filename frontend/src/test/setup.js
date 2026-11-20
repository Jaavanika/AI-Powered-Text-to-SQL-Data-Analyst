import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  try {
    localStorage.clear()
  } catch {
    /* storage unavailable */
  }
})

// jsdom has no layout engine or these browser APIs.
globalThis.ResizeObserver = globalThis.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} }
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
