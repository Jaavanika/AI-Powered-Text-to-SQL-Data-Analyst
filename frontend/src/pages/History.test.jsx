import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { barAnalysis, historyItem, historyPage, mockApi, okResponse } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))
vi.mock('../components/ChartViewer.jsx', () => ({
  default: ({ spec }) => <figure data-testid="chart">{spec.type} chart</figure>,
}))

import History from './History.jsx'

const navigate = vi.fn()
const open = () => renderInApp(<History navigate={navigate} />)
const lastCall = () => api.history.mock.calls.at(-1)
const row = async (question) => (await screen.findByRole('heading', { name: question })).closest('li')

const ok = historyItem()
const unsure = historyItem({ id: 'h2', question: 'Sales by region', status: 'clarification', sql: null, summary: null,
  clarification: 'Which region?', options: ['North', 'South'], row_count: null, has_result: false })
const failed = historyItem({ id: 'h3', question: 'Do something odd', status: 'failed', sql: null, summary: null,
  error: 'I could not produce a valid query.', row_count: null })

beforeEach(() => {
  Object.assign(api, mockApi())
  api.history.mockResolvedValue(historyPage([ok, unsure, failed]))
  navigate.mockClear()
})

describe('history list', () => {
  it('lists every question for the selected data source, with its status and a summary', async () => {
    open()
    const first = await row('Revenue by category?')
    expect(within(first).getByText('Answered')).toBeInTheDocument()
    expect(within(first).getByText(/3 rows/)).toBeInTheDocument()
    expect(within(first).getByText('Electronics led with 600.')).toBeInTheDocument()
    expect(within(await row('Sales by region')).getByText('Needed a detail')).toBeInTheDocument()
    expect(within(await row('Sales by region')).getByText('Which region?')).toBeInTheDocument()
    expect(within(await row('Do something odd')).getByText('Failed')).toBeInTheDocument()
    expect(within(await row('Do something odd')).getByText('I could not produce a valid query.')).toBeInTheDocument()
    expect(screen.getByText('3 questions')).toBeInTheDocument()
    expect(api.history).toHaveBeenCalledWith('sample', expect.objectContaining({ limit: 20, offset: 0 }))
  })

  it('says so when there is nothing yet, and offers no Clear button then', async () => {
    api.history.mockResolvedValue(historyPage([]))
    open()
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear history' })).toBeDisabled()
  })

  it('explains what is kept: questions for the retention period, and whether rows are saved', async () => {
    open()
    expect(await screen.findByText(/kept for 90 days/)).toBeInTheDocument()
    expect(screen.getByText(/Result rows are not saved/)).toBeInTheDocument()
  })

  it('says how many rows are saved when the server keeps them, and marks those answers', async () => {
    api.history.mockResolvedValue(historyPage([historyItem({ has_result: true })], { retention: { retain_rows: 200, retention_days: 0 } }))
    open()
    expect(await screen.findByText(/first 200 rows of each answer are saved/)).toBeInTheDocument()
    expect(screen.getByText(/kept until you delete them/)).toBeInTheDocument()
    expect(within(await row('Revenue by category?')).getByText('results kept')).toBeInTheDocument()
  })

  it('shows the SQL on demand, and has no SQL for a clarification', async () => {
    open()
    const first = await row('Revenue by category?')
    await userEvent.click(within(first).getByRole('button', { name: /How this was answered/ }))
    expect(within(first).getByLabelText('Generated SQL')).toHaveTextContent('SELECT category, SUM(x)')
    expect(within(await row('Sales by region')).queryByRole('button', { name: /How this was answered/ })).not.toBeInTheDocument()
  })

  it('shows an error when the history cannot be loaded', async () => {
    api.history.mockRejectedValue(new Error('Could not reach the server.'))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
  })
})

describe('searching and filtering', () => {
  it('searches after a short pause and restarts from the first page', async () => {
    open()
    await row('Revenue by category?')
    api.history.mockResolvedValue(historyPage([unsure]))
    await userEvent.type(screen.getByLabelText('Search'), 'region')
    await waitFor(() => expect(lastCall()[1]).toMatchObject({ q: 'region', offset: 0 }))
    expect(await row('Sales by region')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Revenue by category?' })).not.toBeInTheDocument()
    expect(screen.getByText('1 matching question')).toBeInTheDocument()
    // typing five letters does not mean five requests
    expect(api.history.mock.calls.filter(([, o]) => o.q).length).toBe(1)
  })

  it('filters by status', async () => {
    open()
    await row('Revenue by category?')
    api.history.mockResolvedValue(historyPage([failed]))
    await userEvent.click(screen.getByRole('button', { name: 'Failed' }))
    await waitFor(() => expect(lastCall()[1]).toMatchObject({ status: 'failed' }))
    expect(screen.getByRole('button', { name: 'Failed' })).toHaveAttribute('aria-pressed', 'true')
    expect(await row('Do something odd')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'All' }))
    await waitFor(() => expect(lastCall()[1].status).toBeNull())
  })

  it('tells a search with no hits apart from an empty history', async () => {
    open()
    await row('Revenue by category?')
    api.history.mockResolvedValue(historyPage([]))
    await userEvent.type(screen.getByLabelText('Search'), 'zzz')
    expect(await screen.findByText('No questions match.')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing here yet/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear history' })).toBeEnabled()
  })
})

describe('paging', () => {
  it('loads the next page and adds it to the list', async () => {
    const many = Array.from({ length: 20 }, (_, i) => historyItem({ id: `p${i}`, question: `Question ${i}` }))
    api.history
      .mockResolvedValueOnce(historyPage(many, { total: 22 }))
      .mockResolvedValueOnce(historyPage([historyItem({ id: 'x1', question: 'Older one' }), historyItem({ id: 'x2', question: 'Oldest one' })], { total: 22 }))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Show more (2 left)' }))
    await waitFor(() => expect(lastCall()[1]).toMatchObject({ offset: 20 }))
    expect(await screen.findByText('Oldest one')).toBeInTheDocument()
    expect(screen.getByText('Question 0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument()
  })

  it('does not offer more when everything is already shown', async () => {
    open()
    await row('Revenue by category?')
    expect(screen.queryByRole('button', { name: /Show more/ })).not.toBeInTheDocument()
  })
})

describe('using an old question', () => {
  it('asks it again in a new chat on the Ask page', async () => {
    open()
    await userEvent.click(within(await row('Revenue by category?')).getByRole('button', { name: 'Ask again' }))
    expect(navigate).toHaveBeenCalledWith('ask')
  })

  it('opens the conversation it came from', async () => {
    open()
    await userEvent.click(within(await row('Sales by region')).getByRole('button', { name: 'Open conversation' }))
    expect(navigate).toHaveBeenCalledWith('ask')
  })

  it('runs the saved SQL again when no rows were kept, and shows the chart', async () => {
    api.execute.mockResolvedValue({ validation: { valid: true }, result: okResponse().result })
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: barAnalysis.chart })
    open()
    await userEvent.click(within(await row('Revenue by category?')).getByRole('button', { name: 'Run SQL again' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.execute).toHaveBeenCalledWith('sample', ok.sql)
    expect(api.keptResult).not.toHaveBeenCalled()
  })

  it('shows the saved rows when they were kept', async () => {
    api.history.mockResolvedValue(historyPage([historyItem({ has_result: true })]))
    api.keptResult.mockResolvedValue(okResponse().result)
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: barAnalysis.chart })
    open()
    await userEvent.click(within(await row('Revenue by category?')).getByRole('button', { name: 'Show saved results' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.keptResult).toHaveBeenCalledWith('h1')
    expect(api.execute).not.toHaveBeenCalled()
  })

  it('reports a failed re-run in place, without losing the list', async () => {
    api.execute.mockRejectedValue(new Error('The query was cancelled: it ran too long.'))
    open()
    const first = await row('Revenue by category?')
    await userEvent.click(within(first).getByRole('button', { name: 'Run SQL again' }))
    expect(await within(first).findByRole('alert')).toHaveTextContent('ran too long')
    expect(within(first).getByRole('button', { name: 'Run SQL again' })).toBeEnabled()
  })

  it('offers no re-run for a question that never produced SQL', async () => {
    open()
    const unclear = await row('Sales by region')
    expect(within(unclear).queryByRole('button', { name: /Run SQL again|Show saved results/ })).not.toBeInTheDocument()
  })
})

describe('deleting', () => {
  it('deletes one question after confirmation, and only then', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    open()
    const remove = within(await row('Do something odd')).getByRole('button', { name: /Delete “Do something odd”/ })
    confirm.mockReturnValueOnce(false)
    await userEvent.click(remove)
    expect(api.deleteTurn).not.toHaveBeenCalled()
    confirm.mockReturnValueOnce(true)
    await userEvent.click(remove)
    await waitFor(() => expect(api.deleteTurn).toHaveBeenCalledWith('h3'))
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Do something odd' })).not.toBeInTheDocument())
    expect(screen.getByText('2 questions')).toBeInTheDocument()
  })

  it('mentions saved results in the warning when there are some', async () => {
    api.history.mockResolvedValue(historyPage([historyItem({ has_result: true })]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    open()
    await userEvent.click(within(await row('Revenue by category?')).getByRole('button', { name: /Delete/ }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('saved results'))
  })

  it('clears everything for the data source after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    open()
    await row('Revenue by category?')
    confirm.mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    expect(api.clearHistory).not.toHaveBeenCalled()
    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Clear history' }))
    await waitFor(() => expect(api.clearHistory).toHaveBeenCalledWith('sample'))
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument()
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('Sample business database'))
  })

  it('shows the error and keeps the question if deleting fails', async () => {
    api.deleteTurn.mockRejectedValue(new Error('Question not found.'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    open()
    await userEvent.click(within(await row('Do something odd')).getByRole('button', { name: /Delete/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Question not found.')
    expect(screen.getByRole('heading', { name: 'Do something odd' })).toBeInTheDocument()
  })
})
