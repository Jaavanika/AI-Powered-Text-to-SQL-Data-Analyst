import { useEffect } from 'react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { barAnalysis, clarificationResponse, mockApi, okResponse, uploadSource } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'
import { useApp } from '../AppContext.jsx'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))
// The real chart needs layout; its drawing is covered in ChartViewer.test.jsx.
vi.mock('../components/ChartViewer.jsx', () => ({
  default: ({ spec }) => <figure data-testid="chart">{spec.type} chart</figure>,
}))

import AnalyticsWorkspace from './AnalyticsWorkspace.jsx'

const navigate = vi.fn()
const open = () => renderInApp(<AnalyticsWorkspace navigate={navigate} />)
const ask = async (text) => {
  await userEvent.type(await screen.findByLabelText('Your question'), `${text}{Enter}`)
}

beforeEach(() => {
  Object.assign(api, mockApi())
  api.chat.mockResolvedValue(okResponse())
})

describe('first screen', () => {
  it('offers starter questions for the selected data source', async () => {
    open()
    expect(await screen.findByRole('heading', { name: /Ask a question about Sample business database/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'How many customers are there?' })).toBeEnabled()
  })

  it('uses questions built from an uploaded table\'s columns', async () => {
    localStorage.setItem('tsql.datasource', uploadSource.id)
    open()
    expect(await screen.findByRole('button', { name: 'How many rows are in sales?' })).toBeInTheDocument()
  })

  it('warns, and blocks asking, when the server has no LLM configured', async () => {
    api.health.mockResolvedValue({ status: 'ok', llm_configured: false })
    open()
    expect(await screen.findByText(/AI model is not configured on the server/)).toBeInTheDocument()
    expect(screen.getByLabelText('Your question')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'How many customers are there?' })).toBeDisabled()
  })
})

describe('asking', () => {
  it('shows the answer, then the summary, with the chart and a validated badge', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'How many customers are there?' }))

    expect(await screen.findByTestId('chart')).toHaveTextContent('bar chart')
    expect(await screen.findByText('Electronics led with 600.')).toBeInTheDocument()
    expect(screen.getByText(/Every figure in it was checked against the result/)).toBeInTheDocument()
    expect(screen.getByText(/Validated · read-only/)).toBeInTheDocument()
    expect(api.chat).toHaveBeenCalledWith(expect.objectContaining({ message: 'How many customers are there?', include_summary: false }), expect.anything())
  })

  it('submits with Enter, keeps Shift+Enter for new lines, and clears the box', async () => {
    open()
    const box = await screen.findByLabelText('Your question')
    await userEvent.type(box, 'line one{Shift>}{Enter}{/Shift}line two')
    expect(box).toHaveValue('line one\nline two')
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(api.chat).toHaveBeenCalledOnce())
    expect(api.chat.mock.calls[0][0].message).toBe('line one\nline two')
    expect(box).toHaveValue('')
  })

  it('disables Ask for empty input and while a question is running', async () => {
    let resolve
    api.chat.mockReturnValue(new Promise((r) => { resolve = r }))
    open()
    const button = await screen.findByRole('button', { name: 'Ask' })
    expect(button).toBeDisabled()
    await ask('first')
    expect(await screen.findByRole('status')).toHaveTextContent('Working on it')
    await userEvent.type(screen.getByLabelText('Your question'), 'second')
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    resolve(okResponse())
    await screen.findByTestId('chart')
  })

  it('shows an error with a retry that works', async () => {
    api.chat.mockRejectedValueOnce(new Error('Could not reach the server. Is the backend running?'))
    open()
    await ask('hello')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.chat).toHaveBeenCalledTimes(2)
  })

  it('explains a failed answer and shows what was tried', async () => {
    api.chat.mockResolvedValue(okResponse({
      status: 'failed', result: null, analysis: null, error: 'No valid query was produced after 3 attempt(s).',
      sql: 'SELECT nope FROM t', attempts: [{ sql: 'SELECT nope FROM t', error: 'Unknown column.' }], validation: null,
    }))
    open()
    await ask('something impossible')
    expect(await screen.findByText(/couldn’t produce a valid query/)).toBeInTheDocument()
    expect(screen.getByText(/No valid query was produced after 3 attempt/)).toBeInTheDocument()
    expect(api.summary).not.toHaveBeenCalled()
  })

  it('says when a query returned no rows', async () => {
    api.chat.mockResolvedValue(okResponse({ result: { ...okResponse().result, rows: [], row_count: 0 }, analysis: { ...barAnalysis, chart: null } }))
    open()
    await ask('nothing matches')
    expect(await screen.findByText('The query ran but returned no rows.')).toBeInTheDocument()
  })
})

describe('clarification', () => {
  it('offers the options as buttons and sends the chosen one in the same conversation', async () => {
    api.chat.mockResolvedValueOnce(clarificationResponse)
    open()
    await ask('Show me the best-performing product.')
    expect(await screen.findByText('How should best-performing be measured?')).toBeInTheDocument()

    api.chat.mockResolvedValueOnce(okResponse())
    await userEvent.click(screen.getByRole('button', { name: 'Highest total revenue' }))

    await screen.findByTestId('chart')
    expect(api.chat.mock.calls[1][0]).toMatchObject({ message: 'Highest total revenue', conversation_id: 'c1' })
    // the earlier clarification can no longer be answered
    expect(screen.getByRole('button', { name: 'Highest total revenue', hidden: true, description: '' })).toBeDisabled()
  })

  it('does not ask for a summary of a clarification', async () => {
    api.chat.mockResolvedValue(clarificationResponse)
    open()
    await ask('best product')
    await screen.findByText(/I need one detail/)
    expect(api.summary).not.toHaveBeenCalled()
  })
})

describe('switching the chart', () => {
  it('redraws from the same rows without asking the model again', async () => {
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: { ...barAnalysis.chart, type: 'pie', alternatives: ['bar', 'table'] } })
    open()
    await ask('revenue by category')
    expect(await screen.findByTestId('chart')).toHaveTextContent('bar chart')

    await userEvent.click(screen.getByRole('button', { name: 'Pie' }))
    await waitFor(() => expect(screen.getByTestId('chart')).toHaveTextContent('pie chart'))
    expect(api.chart).toHaveBeenCalledWith(expect.objectContaining({
      chart_type: 'pie', columns: ['category', 'revenue'], question: 'revenue by category',
    }))
    expect(api.chat).toHaveBeenCalledOnce()
  })

  it('shows the table twin, and says when a switch failed', async () => {
    api.chart.mockRejectedValue(new Error('A pie chart needs more categories.'))
    open()
    await ask('revenue by category')
    await screen.findByTestId('chart')

    await userEvent.click(screen.getByRole('button', { name: 'Pie' }))
    expect(await screen.findByText('A pie chart needs more categories.')).toBeInTheDocument()
    expect(screen.getByTestId('chart')).toHaveTextContent('bar chart')       // stays on what worked

    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(screen.getByRole('table')).toHaveTextContent('Electronics')
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument()
  })
})

describe('history', () => {
  const item = { id: 'c1', datasource_id: 'sample', title: 'Revenue by category', created_at: '2026-11-19T10:00:00Z', updated_at: '2026-11-19T10:00:00Z', turn_count: 2 }

  it('lists saved conversations and reopens one with its stored summary', async () => {
    api.listConversations.mockResolvedValue([item])
    api.getConversation.mockResolvedValue({ ...item, turns: [
      { id: 't1', question: 'Revenue by category', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 4, summary: 'Stored summary.', options: [], tables_used: ['orders'] }] })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /^Revenue by category/ }))
    expect(await screen.findByText('Stored summary.')).toBeInTheDocument()
    expect(screen.getByText(/4 rows were not kept/)).toBeInTheDocument()
    expect(api.listConversations).toHaveBeenCalledWith('sample')
  })

  it('brings the results back on request', async () => {
    api.listConversations.mockResolvedValue([item])
    api.getConversation.mockResolvedValue({ ...item, turns: [
      { id: 't1', question: 'Revenue by category', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 3, summary: null, options: [], tables_used: [] }] })
    api.execute.mockResolvedValue({ validation: { valid: true }, result: okResponse().result })
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: barAnalysis.chart })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /^Revenue by category/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Show results' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.execute).toHaveBeenCalledWith('sample', 'SELECT 1')
  })

  it('deletes a conversation after confirmation, and only then', async () => {
    api.listConversations.mockResolvedValue([item])
    const confirm = vi.spyOn(window, 'confirm')
    open()
    const remove = await screen.findByRole('button', { name: /Delete conversation Revenue by category/ })
    confirm.mockReturnValueOnce(false)
    await userEvent.click(remove)
    expect(api.deleteConversation).not.toHaveBeenCalled()
    confirm.mockReturnValueOnce(true)
    await userEvent.click(remove)
    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(screen.queryByRole('button', { name: /Delete conversation/ })).not.toBeInTheDocument())
  })

  it('refreshes the list after a new question and starts a fresh chat on request', async () => {
    open()
    await ask('How many customers?')
    await screen.findByTestId('chart')
    await waitFor(() => expect(api.listConversations.mock.calls.length).toBeGreaterThanOrEqual(2))
    await userEvent.click(screen.getByRole('button', { name: /New chat/ }))
    expect(await screen.findByRole('heading', { name: /Ask a question about/ })).toBeInTheDocument()
  })

  it('can be collapsed', async () => {
    api.listConversations.mockResolvedValue([item])
    open()
    const nav = await screen.findByRole('navigation', { name: 'Conversations' })
    const toggle = within(nav).getByRole('button', { name: /History/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(await within(nav).findByRole('button', { name: /^Revenue by category/ })).toBeInTheDocument()
    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    // collapsed means the conversations are really gone from the page, not just flagged
    expect(within(nav).queryByRole('button', { name: /^Revenue by category/ })).not.toBeInTheDocument()
    await userEvent.click(toggle)
    expect(within(nav).getByRole('button', { name: /^Revenue by category/ })).toBeInTheDocument()
  })
})

describe('definitions', () => {
  it('says which of your definitions an answer followed', async () => {
    api.chat.mockResolvedValue(okResponse({ definitions_used: ['Revenue', 'Best-performing product'] }))
    open()
    await ask('total revenue')
    const line = await screen.findByText(/Followed your definitions of/)
    expect(within(line).getByText('Revenue')).toBeInTheDocument()
    expect(within(line).getByText('Best-performing product')).toBeInTheDocument()
  })

  it('says nothing when no definition applied', async () => {
    api.chat.mockResolvedValue(okResponse({ definitions_used: [] }))
    open()
    await ask('how many customers')
    await screen.findByTestId('chart')
    expect(screen.queryByText(/Followed your/)).not.toBeInTheDocument()
  })
})

describe('saved results', () => {
  const item = { id: 'c1', datasource_id: 'sample', title: 'Revenue by category', created_at: '2026-11-19T10:00:00Z', updated_at: '2026-11-19T10:00:00Z', turn_count: 1 }
  const turn = (extra) => ({ id: 't1', question: 'Revenue by category', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 3, summary: null, options: [], tables_used: [], has_result: true, ...extra })
  const reopen = async () => {
    api.listConversations.mockResolvedValue([item])
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: barAnalysis.chart })
    open()
    await userEvent.click(await screen.findByRole('button', { name: /^Revenue by category/ }))
  }

  it('shows the rows that were kept without running the query again', async () => {
    api.getConversation.mockResolvedValue({ ...item, turns: [turn()] })
    api.keptResult.mockResolvedValue(okResponse().result)
    await reopen()
    expect(await screen.findByText(/rows saved with this answer can be shown again/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show results' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.keptResult).toHaveBeenCalledWith('t1')
    expect(api.execute).not.toHaveBeenCalled()
  })

  it('runs the saved query again if the kept rows are gone', async () => {
    api.getConversation.mockResolvedValue({ ...item, turns: [turn()] })
    api.keptResult.mockRejectedValue(new Error('No result was kept for this answer.'))
    api.execute.mockResolvedValue({ validation: { valid: true }, result: okResponse().result })
    await reopen()
    await userEvent.click(await screen.findByRole('button', { name: 'Show results' }))
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.execute).toHaveBeenCalledWith('sample', 'SELECT 1')
  })

  it('never asks for kept rows when none were kept', async () => {
    api.getConversation.mockResolvedValue({ ...item, turns: [turn({ has_result: false })] })
    api.execute.mockResolvedValue({ validation: { valid: true }, result: okResponse().result })
    await reopen()
    await userEvent.click(await screen.findByRole('button', { name: 'Show results' }))
    await screen.findByTestId('chart')
    expect(api.keptResult).not.toHaveBeenCalled()
  })
})

describe('arriving from the history page', () => {
  function Arrive({ handoff }) {
    const { selected, setHandoff } = useApp()
    useEffect(() => { if (selected) setHandoff({ datasourceId: selected.id, ...handoff }) }, [selected, handoff, setHandoff])
    return <AnalyticsWorkspace navigate={navigate} />
  }

  it('reopens the conversation it was sent to', async () => {
    const convo = { id: 'c9', datasource_id: 'sample', title: 'Older chat', created_at: '2026-11-19T10:00:00Z', updated_at: '2026-11-19T10:00:00Z', turn_count: 1,
      turns: [{ id: 't9', question: 'Older question', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 1, summary: 'Stored.', options: [], tables_used: [] }] }
    api.getConversation.mockResolvedValue(convo)
    renderInApp(<Arrive handoff={{ open: 'c9' }} />)
    expect(await screen.findByText('Stored.')).toBeInTheDocument()
    expect(api.getConversation).toHaveBeenCalledWith('c9')
    expect(api.getConversation).toHaveBeenCalledTimes(1)
  })

  it('asks the question again, once', async () => {
    renderInApp(<Arrive handoff={{ ask: 'Revenue by category?' }} />)
    expect(await screen.findByTestId('chart')).toBeInTheDocument()
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.chat).toHaveBeenCalledWith(expect.objectContaining({ message: 'Revenue by category?', conversation_id: null }), expect.anything())
  })

  it('ignores a handoff meant for another data source', async () => {
    function Wrong() {
      const { selected, setHandoff } = useApp()
      useEffect(() => { if (selected) setHandoff({ datasourceId: 'someone-else', ask: 'x' }) }, [selected, setHandoff])
      return <AnalyticsWorkspace navigate={navigate} />
    }
    renderInApp(<Wrong />)
    await screen.findByRole('heading', { name: /Ask a question about/ })
    expect(api.chat).not.toHaveBeenCalled()
  })
})
