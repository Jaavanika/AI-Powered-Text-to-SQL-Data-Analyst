import { useEffect, useRef, useState } from 'react'
import AnswerCard from './AnswerCard.jsx'

const MAX_LENGTH = 1000

function Composer({ busy, disabled, placeholder, onSend }) {
  const [text, setText] = useState('')
  const ref = useRef(null)

  const submit = () => {
    const value = text.trim()
    if (!value || busy || disabled) return
    onSend(value)
    setText('')
  }

  // Grow with the text, up to a few lines.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [text])

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <label htmlFor="question" className="sr-only">Your question</label>
      <textarea
        id="question" ref={ref} rows={1} value={text} maxLength={MAX_LENGTH} disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <button type="submit" className="btn btn--primary" disabled={!text.trim() || busy || disabled}>
        Ask
      </button>
      {text.length > MAX_LENGTH - 100 && (
        <span className="composer__count muted">{text.length}/{MAX_LENGTH}</span>
      )}
    </form>
  )
}

export default function ChatInterface({
  datasource, messages, busy, suggestions = [], llmReady = true,
  onSend, onStop, onRetry, onAnalysis, onShowResults,
}) {
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [messages])

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')

  return (
    <div className="chat">
      <div className="chat__scroll">
        {messages.length === 0 ? (
          <div className="chat__empty">
            <h2>Ask a question about {datasource ? datasource.name : 'your data'}</h2>
            <p className="muted">
              Ask in plain language. The SQL is generated, checked and run read-only, then you get a table, a chart and a short summary.
            </p>
            {suggestions.length > 0 && (
              <ul className="suggestions" aria-label="Suggested questions">
                {suggestions.map((q) => (
                  <li key={q}>
                    <button type="button" className="suggestion" disabled={busy || !llmReady} onClick={() => onSend(q)}>{q}</button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <ol className="messages" aria-label="Conversation">
            {messages.map((m) => (
              <li key={m.id} className={`message message--${m.role}`}>
                {m.role === 'user' ? (
                  <p className="bubble">{m.text}</p>
                ) : (
                  <AnswerCard
                    message={m} isLatest={m === lastAssistant}
                    onSend={onSend} onRetry={onRetry} onStop={onStop}
                    onAnalysis={onAnalysis} onShowResults={onShowResults}
                  />
                )}
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>
      <Composer
        busy={busy} disabled={!datasource || !llmReady}
        placeholder={!llmReady ? 'The AI model is not configured on the server.' : 'Ask a question about your data…'}
        onSend={onSend}
      />
    </div>
  )
}
