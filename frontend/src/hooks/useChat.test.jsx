import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { barAnalysis, barResult, clarificationResponse, mockApi, okResponse } from '../test/fixtures.js'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))

import { useChat } from './useChat.js'

beforeEach(() => {
  Object.assign(api, mockApi())
  api.chat.mockResolvedValue(okResponse())
})

const setup = (id = 'sample', options) => renderHook(({ ds }) => useChat(ds, options), { initialProps: { ds: id } })
const send = (result, text) => act(async () => { await result.current.send(text) })
const assistant = (result) => result.current.messages.find((m) => m.role === 'assistant')

describe('sending a question', () => {
  it('adds the question, then the answer with its analysis, and asks for no summary up front', async () => {
    const onChanged = vi.fn()
    const { result } = setup('sample', { onChanged })
    await send(result, '  Revenue by category?  ')

    expect(result.current.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(result.current.messages[0].text).toBe('Revenue by category?')
    expect(assistant(result).state).toBe('done')
    expect(assistant(result).analysis).toEqual(barAnalysis)
    expect(result.current.conversationId).toBe('c1')
    expect(result.current.busy).toBe(false)
    expect(onChanged).toHaveBeenCalled()
    expect(api.chat).toHaveBeenCalledWith(
      { datasource_id: 'sample', message: 'Revenue by category?', conversation_id: null, include_summary: false },
      expect.any(AbortSignal))
  })

  it('shows the pending state while waiting', async () => {
    let resolve
    api.chat.mockReturnValue(new Promise((r) => { resolve = r }))
    const { result } = setup()
    act(() => { result.current.send('slow question') })
    await waitFor(() => expect(assistant(result)?.state).toBe('pending'))
    expect(result.current.busy).toBe(true)
    await act(async () => { resolve(okResponse()) })
    expect(assistant(result).state).toBe('done')
  })

  it('fetches the summary afterwards and slots it into the same message', async () => {
    const { result } = setup()
    await send(result, 'q')
    await waitFor(() => expect(assistant(result).summary).toEqual({ state: 'done', text: 'Electronics led with 600.', source: 'llm' }))
    expect(api.summary).toHaveBeenCalledWith({
      columns: barResult.columns, rows: barResult.rows, truncated: false, question: 'q', turn_id: 't1',
    })
  })

  it('keeps the answer when the summary fails', async () => {
    api.summary.mockRejectedValue(new Error('LLM down'))
    const { result } = setup()
    await send(result, 'q')
    await waitFor(() => expect(assistant(result).summary.state).toBe('error'))
    expect(assistant(result).state).toBe('done')
    expect(assistant(result).analysis.chart.type).toBe('bar')
  })

  it('continues the same conversation on the next message', async () => {
    const { result } = setup()
    await send(result, 'first')
    api.chat.mockResolvedValue(okResponse({ turn_id: 't2' }))
    await send(result, 'second')
    expect(api.chat.mock.calls[1][0].conversation_id).toBe('c1')
    expect(result.current.messages).toHaveLength(4)
  })

  it('does not ask for a summary for clarifications or empty results', async () => {
    api.chat.mockResolvedValue(clarificationResponse)
    const { result } = setup()
    await send(result, 'best product')
    expect(assistant(result).summary).toEqual({ state: 'idle' })
    api.chat.mockResolvedValue(okResponse({ result: { ...barResult, rows: [], row_count: 0 } }))
    await send(result, 'nothing matches')
    expect(api.summary).not.toHaveBeenCalled()
  })

  it('ignores blank questions and a missing data source', async () => {
    const { result } = setup()
    await send(result, '   ')
    expect(api.chat).not.toHaveBeenCalled()
    const none = setup(null)                                    // no data source selected
    await send(none.result, 'hello')
    expect(api.chat).not.toHaveBeenCalled()
  })
})

describe('failures', () => {
  it('shows the error on the answer and replaces that same message on retry', async () => {
    api.chat.mockRejectedValueOnce(new Error('Could not reach the server.'))
    const { result } = setup()
    await send(result, 'q')
    const failed = assistant(result)
    expect(failed).toMatchObject({ state: 'error', error: 'Could not reach the server.' })
    expect(result.current.busy).toBe(false)

    await act(async () => { await result.current.retry(failed) })
    expect(result.current.messages).toHaveLength(2)             // one question, one (now successful) answer
    expect(assistant(result).id).toBe(failed.id)
    expect(assistant(result).state).toBe('done')
  })

  it('stops a running request and says so', async () => {
    api.chat.mockImplementation((_, signal) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    }))
    const { result } = setup()
    act(() => { result.current.send('slow') })
    await waitFor(() => expect(result.current.busy).toBe(true))
    act(() => result.current.stop())
    await waitFor(() => expect(assistant(result)).toMatchObject({ state: 'error', error: 'Stopped.' }))
    expect(result.current.busy).toBe(false)
  })
})

describe('switching and resuming', () => {
  it('starts over when the data source changes', async () => {
    const { result, rerender } = setup('sample')
    await send(result, 'q')
    expect(result.current.messages).toHaveLength(2)
    rerender({ ds: 'up1' })
    await waitFor(() => expect(result.current.messages).toEqual([]))
    expect(result.current.conversationId).toBeNull()
  })

  it('opens a saved conversation from its turns, restoring saved summaries', async () => {
    api.getConversation.mockResolvedValue({
      id: 'c9', turns: [
        { id: 't1', question: 'Q1', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 1, summary: 'One row.', options: [] },
        { id: 't2', question: 'Q2', status: 'clarification', clarification: 'Which?', options: ['a', 'b'], summary: null },
      ],
    })
    const { result } = setup()
    await act(async () => { await result.current.open('c9') })
    expect(result.current.conversationId).toBe('c9')
    expect(result.current.messages.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(result.current.messages[1].summary).toEqual({ state: 'done', text: 'One row.', source: 'llm' })
    expect(result.current.messages[3].summary).toEqual({ state: 'idle' })

    await send(result, 'next')                                  // continues that conversation
    expect(api.chat.mock.calls[0][0].conversation_id).toBe('c9')
  })

  it('re-runs a saved query through the safe execute path to bring the rows back', async () => {
    api.getConversation.mockResolvedValue({ id: 'c9', turns: [
      { id: 't1', question: 'Q1', status: 'ok', sql: 'SELECT 1', columns: ['n'], row_count: 1, summary: null, options: [] }] })
    api.execute.mockResolvedValue({ validation: { valid: true }, result: barResult })
    api.chart.mockResolvedValue({ profile: barAnalysis.profile, chart: barAnalysis.chart })
    const { result } = setup()
    await act(async () => { await result.current.open('c9') })
    const turnMessage = result.current.messages[1]

    await act(async () => { await result.current.showResults(turnMessage) })
    expect(api.execute).toHaveBeenCalledWith('sample', 'SELECT 1')
    expect(api.chart).toHaveBeenCalledWith(expect.objectContaining({ columns: barResult.columns, question: 'Q1' }))
    expect(result.current.messages[1]).toMatchObject({ restoring: false, result: barResult })
    expect(result.current.messages[1].analysis.chart.type).toBe('bar')
  })

  it('reports when a saved query can no longer run', async () => {
    api.getConversation.mockResolvedValue({ id: 'c9', turns: [
      { id: 't1', question: 'Q1', status: 'ok', sql: 'SELECT nope', columns: [], row_count: 1, summary: null, options: [] }] })
    api.execute.mockRejectedValue(new Error('The SQL failed validation. Unknown column.'))
    const { result } = setup()
    await act(async () => { await result.current.open('c9') })
    await act(async () => { await result.current.showResults(result.current.messages[1]) })
    expect(result.current.messages[1]).toMatchObject({ restoring: false, restoreError: 'The SQL failed validation. Unknown column.' })
  })

  it('starts a new chat', async () => {
    const { result } = setup()
    await send(result, 'q')
    act(() => result.current.newChat())
    expect(result.current.messages).toEqual([])
    expect(result.current.conversationId).toBeNull()
  })
})

describe('chart edits', () => {
  it('lets a chart-type switch replace the analysis of one message', async () => {
    const { result } = setup()
    await send(result, 'q')
    const next = { ...barAnalysis, chart: { ...barAnalysis.chart, type: 'pie' } }
    act(() => result.current.setAnalysis(assistant(result).id, next))
    expect(assistant(result).analysis.chart.type).toBe('pie')
  })
})
