import { render } from '@testing-library/react'
import { AppProvider } from '../AppContext.jsx'

/** Render a page inside the real app context (data sources come from the mocked API). */
export function renderInApp(ui) {
  return render(<AppProvider>{ui}</AppProvider>)
}
