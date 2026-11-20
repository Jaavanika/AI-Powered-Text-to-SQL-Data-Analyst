import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApp } from '../AppContext.jsx'
import { useChat } from '../hooks/useChat.js'
import ChatInterface from '../components/ChatInterface.jsx'
import QueryHistory from '../components/QueryHistory.jsx'
import { suggestionsFor } from '../lib/suggestions.js'

export default function AnalyticsWorkspace({ navigate }) {
  const { selected, health, loading, handoff, setHandoff } = useApp()
  const [refreshKey, setRefreshKey] = useState(0)
  const [openError, setOpenError] = useState(null)
  const refreshHistory = useCallback(() => setRefreshKey((k) => k + 1), [])

  const chat = useChat(selected?.id, { onChanged: refreshHistory })
  const suggestions = useMemo(() => suggestionsFor(selected), [selected])
  const llmReady = health ? health.llm_configured !== false : true

  // The History page can send us here to reopen a conversation or ask a question again.
  const { open: openConversation, send } = chat
  useEffect(() => {
    if (!handoff || handoff.datasourceId !== selected?.id) return
    setHandoff(null)
    if (handoff.open) openConversation(handoff.open).catch((e) => setOpenError(e.message))
    else if (handoff.ask) send(handoff.ask)
  }, [handoff, selected?.id, setHandoff, openConversation, send])

  if (!selected) {
    return (
      <div className="page page--center">
        <p className="muted">{loading ? 'Loading your data sources…' : 'Choose a data source to start.'}</p>
        {!loading && <button type="button" className="btn btn--primary" onClick={() => navigate('data')}>Add data</button>}
      </div>
    )
  }

  const open = async (id) => {
    setOpenError(null)
    try { await chat.open(id) } catch (e) { setOpenError(e.message) }
  }

  return (
    <div className="workspace">
      <aside className="workspace__side">
        <QueryHistory
          datasourceId={selected.id} activeId={chat.conversationId} refreshKey={refreshKey}
          onOpen={open} onNew={chat.newChat}
          onDeleted={(id) => { if (id === chat.conversationId) chat.newChat() }}
        />
      </aside>
      <section className="workspace__main" aria-label="Analytics workspace">
        {!llmReady && (
          <div className="notice notice--warn" role="alert">
            The AI model is not configured on the server, so questions cannot be answered yet. Set the
            keys in <code>backend/.env</code> and restart the backend.
          </div>
        )}
        {openError && <div className="notice notice--error" role="alert">{openError}</div>}
        <ChatInterface
          datasource={selected} messages={chat.messages} busy={chat.busy} suggestions={suggestions} llmReady={llmReady}
          onSend={chat.send} onStop={chat.stop} onRetry={chat.retry}
          onAnalysis={chat.setAnalysis} onShowResults={chat.showResults}
        />
      </section>
    </div>
  )
}
