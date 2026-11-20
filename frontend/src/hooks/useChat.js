import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api.js'
import { loadSavedResult } from '../lib/results.js'

let counter = 0
const nextId = () => `m${++counter}`

const hasRows = (response) => response?.status === 'ok' && response.result?.row_count > 0

/**
 * Conversation state for one data source: the messages on screen, the server-side conversation
 * they belong to, and the requests that move it forward.
 *
 * The chat request asks for no summary so the answer and chart appear straight away; the
 * summary (a further LLM call) is fetched afterwards and slotted into the same message.
 */
export function useChat(datasourceId, { onChanged } = {}) {
  const [messages, setMessages] = useState([])
  const [conversationId, setConversationId] = useState(null)
  const [busy, setBusy] = useState(false)
  const abortRef = useRef(null)
  const conversationRef = useRef(null)
  conversationRef.current = conversationId

  const patch = useCallback((id, changes) => {
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...(typeof changes === 'function' ? changes(m) : changes) } : m)))
  }, [])

  const newChat = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setConversationId(null)
    setBusy(false)
  }, [])

  // A different data source is a different conversation.
  useEffect(() => { newChat() }, [datasourceId, newChat])

  const fetchSummary = useCallback(async (messageId, question, response) => {
    patch(messageId, { summary: { state: 'loading' } })
    try {
      const out = await api.summary({
        columns: response.result.columns, rows: response.result.rows, truncated: response.result.truncated,
        question, turn_id: response.turn_id,
      })
      patch(messageId, { summary: { state: 'done', text: out.summary, source: out.source } })
    } catch {
      patch(messageId, { summary: { state: 'error' } })   // the answer stands without it
    }
  }, [patch])

  const send = useCallback(async (text, { replaceId } = {}) => {
    const question = text.trim()
    if (!question || !datasourceId) return
    const assistantId = replaceId || nextId()
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)

    setMessages((list) => {
      const pending = { id: assistantId, role: 'assistant', state: 'pending', question }
      if (replaceId) return list.map((m) => (m.id === replaceId ? pending : m))
      return [...list, { id: nextId(), role: 'user', text: question }, pending]
    })

    try {
      const response = await api.chat({
        datasource_id: datasourceId, message: question, conversation_id: conversationRef.current,
        include_summary: false,
      }, controller.signal)
      setConversationId(response.conversation_id)
      patch(assistantId, {
        state: 'done', response, analysis: response.analysis,
        summary: hasRows(response) ? { state: 'loading' } : { state: 'idle' },
      })
      onChanged?.()
      if (hasRows(response)) fetchSummary(assistantId, question, response)
    } catch (error) {
      if (error?.name === 'AbortError') {
        patch(assistantId, { state: 'error', error: 'Stopped.' })
      } else {
        patch(assistantId, { state: 'error', error: error.message })
      }
    } finally {
      if (abortRef.current === controller) setBusy(false)
    }
  }, [datasourceId, fetchSummary, onChanged, patch])

  const stop = useCallback(() => abortRef.current?.abort(), [])

  const retry = useCallback((message) => send(message.question, { replaceId: message.id }), [send])

  const setAnalysis = useCallback((id, analysis) => patch(id, { analysis }), [patch])

  const open = useCallback(async (id) => {
    abortRef.current?.abort()
    const conversation = await api.getConversation(id)
    const list = []
    for (const turn of conversation.turns) {
      list.push({ id: nextId(), role: 'user', text: turn.question })
      list.push({
        id: nextId(), role: 'assistant', state: 'done', question: turn.question, turn,
        summary: turn.summary ? { state: 'done', text: turn.summary, source: 'llm' } : { state: 'idle' },
      })
    }
    setMessages(list)
    setConversationId(id)
    setBusy(false)
  }, [])

  // Saved turns keep their SQL and, if the administrator allows it, a few of their rows. Otherwise the SQL is run
  // again, through the same validation and read-only execution as any other query.
  const showResults = useCallback(async (message) => {
    patch(message.id, { restoring: true, restoreError: null })
    try {
      const { result, analysis } = await loadSavedResult(datasourceId, message.turn, message.question)
      patch(message.id, { restoring: false, result, analysis })
    } catch (error) {
      patch(message.id, { restoring: false, restoreError: error.message })
    }
  }, [datasourceId, patch])

  return { messages, conversationId, busy, send, stop, retry, newChat, open, setAnalysis, showResults }
}
