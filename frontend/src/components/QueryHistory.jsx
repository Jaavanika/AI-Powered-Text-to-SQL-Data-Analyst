import { useEffect, useState } from 'react'
import { api } from '../services/api.js'
import { formatDate } from '../lib/format.js'

export default function QueryHistory({ datasourceId, activeId, refreshKey, onOpen, onNew, onDeleted }) {
  const [items, setItems] = useState([])
  const [error, setError] = useState(null)
  // Open on wide screens; collapsed on phones, where the list would crowd out the conversation.
  const [open, setOpen] = useState(() => (typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 861px)').matches : true))

  useEffect(() => {
    if (!datasourceId) return setItems([])
    let cancelled = false
    api.listConversations(datasourceId)
      .then((list) => { if (!cancelled) { setItems(list); setError(null) } })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [datasourceId, refreshKey])

  const remove = async (item) => {
    if (!window.confirm(`Delete the conversation “${item.title}”?`)) return
    try {
      await api.deleteConversation(item.id)
      setItems((list) => list.filter((i) => i.id !== item.id))
      onDeleted?.(item.id)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <nav className="history" aria-label="Conversations">
      <button type="button" className="btn btn--block" onClick={onNew}>＋ New chat</button>
      <h2 className="history__title">
        <button type="button" className="history__toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span> History{items.length > 0 && ` (${items.length})`}
        </button>
      </h2>
      {open && error && <p className="inline-error" role="alert">{error}</p>}
      {open && items.length === 0 && !error && <p className="muted history__empty">Your questions will be listed here.</p>}
      {open && <ul>
        {items.map((item) => (
          <li key={item.id} className={item.id === activeId ? 'is-active' : undefined}>
            <button type="button" className="history__item" aria-current={item.id === activeId ? 'true' : undefined}
              onClick={() => onOpen(item.id)}>
              <span className="history__name">{item.title}</span>
              <span className="muted history__meta">{item.turn_count} {item.turn_count === 1 ? 'question' : 'questions'} · {formatDate(item.updated_at)}</span>
            </button>
            <button type="button" className="icon-btn" aria-label={`Delete conversation ${item.title}`} onClick={() => remove(item)}>✕</button>
          </li>
        ))}
      </ul>}
    </nav>
  )
}
