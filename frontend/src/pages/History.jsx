import { useCallback, useEffect, useRef, useState } from 'react'
import { useApp } from '../AppContext.jsx'
import { api } from '../services/api.js'
import { loadSavedResult } from '../lib/results.js'
import { formatDateTime } from '../lib/format.js'
import ResultPanel from '../components/ResultPanel.jsx'
import SQLViewer from '../components/SQLViewer.jsx'

const PAGE_SIZE = 20
const SEARCH_DELAY_MS = 250
const FILTERS = [[null, 'All'], ['ok', 'Answered'], ['clarification', 'Needed a detail'], ['failed', 'Failed']]
const STATUS = {
  ok: ['Answered', 'chip chip--good'],
  clarification: ['Needed a detail', 'chip chip--accent'],
  failed: ['Failed', 'chip chip--warn'],
}

function retentionNote({ retain_rows: rows, retention_days: days }) {
  const kept = days > 0 ? `Questions and SQL are kept for ${days} days, then deleted.` : 'Questions and SQL are kept until you delete them.'
  const results = rows > 0
    ? `The first ${rows.toLocaleString('en-US')} rows of each answer are saved too, so you can look at them again.`
    : 'Result rows are not saved; to see an old answer’s data, its SQL is run again.'
  return `${kept} ${results}`
}

function HistoryItem({ item, datasourceId, onOpen, onAskAgain, onDelete }) {
  const [state, setState] = useState({ phase: 'idle' })
  const [label, className] = STATUS[item.status] ?? [item.status, 'chip']

  const show = async () => {
    setState({ phase: 'loading' })
    try {
      setState({ phase: 'done', ...(await loadSavedResult(datasourceId, item, item.question)) })
    } catch (e) {
      setState({ phase: 'error', message: e.message })
    }
  }

  return (
    <li className="history-item">
      <div className="history-item__head">
        <h3 className="history-item__question">{item.question}</h3>
        <span className={className}>{label}</span>
      </div>
      <p className="muted history-item__meta">
        {formatDateTime(item.created_at)}
        {item.status === 'ok' && item.row_count != null && ` · ${item.row_count.toLocaleString('en-US')} ${item.row_count === 1 ? 'row' : 'rows'}`}
        {item.has_result && <span className="chip chip--small" title="The rows of this answer were saved">results kept</span>}
      </p>
      {item.status === 'ok' && item.summary && <p className="history-item__summary">{item.summary}</p>}
      {item.status === 'clarification' && <p className="history-item__summary">{item.clarification}</p>}
      {item.status === 'failed' && item.error && <p className="history-item__summary muted">{item.error}</p>}

      <div className="history-item__actions">
        <button type="button" className="btn btn--small" onClick={() => onAskAgain(item)}>Ask again</button>
        <button type="button" className="btn btn--small" onClick={() => onOpen(item)}>Open conversation</button>
        {item.status === 'ok' && item.sql && (
          <button type="button" className="btn btn--small" disabled={state.phase === 'loading'} onClick={show}>
            {state.phase === 'loading' ? 'Loading…' : item.has_result ? 'Show saved results' : 'Run SQL again'}
          </button>
        )}
        <button type="button" className="btn btn--ghost btn--small btn--danger" aria-label={`Delete “${item.question}”`}
          onClick={() => onDelete(item)}>Delete</button>
      </div>

      {state.phase === 'error' && <p className="inline-error" role="alert">{state.message}</p>}
      {state.phase === 'done' && (
        <ResultPanel result={state.result} analysis={state.analysis} question={item.question}
          onAnalysis={(analysis) => setState((s) => ({ ...s, analysis }))} />
      )}
      {item.sql && <SQLViewer sql={item.sql} explanation={item.explanation} tablesUsed={item.tables_used} />}
    </li>
  )
}

export default function History({ navigate }) {
  const { selected, loading, setHandoff } = useApp()
  const datasourceId = selected?.id
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState(null)
  const [page, setPage] = useState(null)               // { items, total, retention }
  const [fetching, setFetching] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const id = setTimeout(() => setQuery(search), SEARCH_DELAY_MS)
    return () => clearTimeout(id)
  }, [search])

  // Bumped whenever the list is restarted (new search, filter or data source), so a late "show more" is dropped.
  const generation = useRef(0)

  const loadMore = useCallback((offset) => {
    const started = generation.current
    setFetching(true)
    return api.history(datasourceId, { q: query, status, limit: PAGE_SIZE, offset })
      .then((next) => {
        if (generation.current !== started) return
        setPage((old) => (old ? { ...next, items: [...old.items, ...next.items] } : next))
        setError(null)
      })
      .catch((e) => { if (generation.current === started) setError(e.message) })
      .finally(() => { if (generation.current === started) setFetching(false) })
  }, [datasourceId, query, status])

  useEffect(() => {
    if (!datasourceId) return undefined
    let current = true
    generation.current += 1
    setFetching(false)
    setPage(null)
    api.history(datasourceId, { q: query, status, limit: PAGE_SIZE, offset: 0 })
      .then((next) => { if (current) { setPage(next); setError(null) } })
      .catch((e) => { if (current) setError(e.message) })
    return () => { current = false }
  }, [datasourceId, query, status])

  if (!selected) {
    return (
      <div className="page page--center">
        <p className="muted">{loading ? 'Loading your data sources…' : 'Choose a data source to see its history.'}</p>
      </div>
    )
  }

  const go = (handoff) => { setHandoff({ datasourceId, ...handoff }); navigate('ask') }

  const remove = async (item) => {
    if (!window.confirm(`Delete this question${item.has_result ? ' and its saved results' : ''}?`)) return
    try {
      await api.deleteTurn(item.id)
      setPage((old) => ({ ...old, total: old.total - 1, items: old.items.filter((i) => i.id !== item.id) }))
    } catch (e) {
      setError(e.message)
    }
  }

  const clearAll = async () => {
    if (!window.confirm(`Delete all history for “${selected.name}”? This removes every question, its SQL and any saved results.`)) return
    try {
      await api.clearHistory(datasourceId)
      setPage((old) => ({ ...old, total: 0, items: [] }))
    } catch (e) {
      setError(e.message)
    }
  }

  const items = page?.items ?? []
  const filtered = Boolean(query.trim() || status)
  const everything = page && page.total === 0 && !filtered

  return (
    <div className="page">
      <header className="page__head">
        <h1>History</h1>
        <p className="muted">Every question asked about <strong>{selected.name}</strong>, newest first.</p>
        {page && <p className="muted history-note">{retentionNote(page.retention)}</p>}
      </header>

      <div className="history-tools">
        <div className="field history-tools__search">
          <label htmlFor="history-search" className="label">Search</label>
          <input id="history-search" type="search" value={search} placeholder="Search questions, SQL and summaries"
            maxLength={200} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map(([value, text]) => (
            <button key={text} type="button" className={status === value ? 'is-active' : undefined}
              aria-pressed={status === value} onClick={() => setStatus(value)}>{text}</button>
          ))}
        </div>
        <button type="button" className="btn btn--ghost btn--small btn--danger" disabled={everything || !page}
          onClick={clearAll}>Clear history</button>
      </div>

      {error && <div className="notice notice--error" role="alert">{error}</div>}
      {!page && !error && <p className="muted" role="status">Loading…</p>}
      {everything && <p className="muted empty">Nothing here yet. Questions you ask will be listed with their SQL.</p>}
      {page && !everything && items.length === 0 && <p className="muted empty">No questions match.</p>}

      {items.length > 0 && (
        <>
          <p className="muted" aria-live="polite">
            {filtered ? `${page.total.toLocaleString('en-US')} matching ${page.total === 1 ? 'question' : 'questions'}`
              : `${page.total.toLocaleString('en-US')} ${page.total === 1 ? 'question' : 'questions'}`}
          </p>
          <ul className="history-list" aria-label="Past questions">
            {items.map((item) => (
              <HistoryItem key={item.id} item={item} datasourceId={datasourceId}
                onOpen={(i) => go({ open: i.conversation_id })} onAskAgain={(i) => go({ ask: i.question })}
                onDelete={remove} />
            ))}
          </ul>
          {items.length < page.total && (
            <button type="button" className="btn history-more" disabled={fetching} onClick={() => loadMore(items.length)}>
              {fetching ? 'Loading…' : `Show more (${page.total - items.length} left)`}
            </button>
          )}
        </>
      )}
    </div>
  )
}
