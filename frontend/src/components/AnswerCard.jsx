import { useEffect, useState } from 'react'
import ResultPanel from './ResultPanel.jsx'
import SQLViewer from './SQLViewer.jsx'

function Pending({ onStop }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [])
  // The server answers in one piece, so this is honest elapsed time, not invented progress steps.
  return (
    <div className="pending" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>Working on it… {seconds}s</span>
      <span className="muted pending__hint">Understanding the question, writing and checking the SQL, then running it read-only.</span>
      {onStop && <button type="button" className="btn btn--ghost btn--small" onClick={onStop}>Stop</button>}
    </div>
  )
}

function Summary({ summary }) {
  if (!summary || summary.state === 'idle' || summary.state === 'error') return null
  if (summary.state === 'loading') {
    return (
      <div className="summary summary--loading" role="status" aria-live="polite">
        <span className="spinner spinner--small" aria-hidden="true" />
        <span className="muted">Writing a summary…</span>
      </div>
    )
  }
  return (
    <div className="summary">
      <p className="summary__text">{summary.text}</p>
      <p className="summary__note muted">
        {summary.source === 'template'
          ? 'Basic summary built from the result. The AI-written one could not be verified against the data.'
          : 'Written by AI. Every figure in it was checked against the result.'}
      </p>
    </div>
  )
}

function Clarification({ response, isLatest, onSend }) {
  return (
    <div className="clarify">
      <p className="clarify__lead">I need one detail before I can answer this.</p>
      <p className="clarify__question">{response.clarification}</p>
      {response.options?.length > 0 && (
        <ul className="clarify__options" aria-label="Suggested answers">
          {response.options.map((option) => (
            <li key={option}>
              <button type="button" className="option" disabled={!isLatest} onClick={() => onSend(option)}>
                {option}
              </button>
            </li>
          ))}
        </ul>
      )}
      {isLatest && <p className="muted clarify__hint">Pick one, or type your own answer below.</p>}
    </div>
  )
}

/** One assistant reply. Renders whichever state the message is in. */
export default function AnswerCard({ message, isLatest, onSend, onRetry, onStop, onAnalysis, onShowResults }) {
  if (message.state === 'pending') return <Pending onStop={onStop} />

  if (message.state === 'error') {
    return (
      <div className="notice notice--error" role="alert">
        <p>{message.error}</p>
        <button type="button" className="btn btn--small" onClick={() => onRetry(message)}>Try again</button>
      </div>
    )
  }

  const { response, turn } = message
  const status = response?.status ?? turn?.status

  if (status === 'clarification') {
    return <Clarification response={response ?? turn} isLatest={isLatest} onSend={onSend} />
  }

  if (status === 'failed') {
    return (
      <div className="notice notice--error" role="alert">
        <p><strong>I couldn’t produce a valid query for this.</strong></p>
        <p className="muted">{response?.error ?? turn?.error}</p>
        {response?.sql && (
          <SQLViewer sql={response.sql} attempts={response.attempts} />
        )}
        <p className="muted">Try rephrasing, or ask about specific columns or tables.</p>
      </div>
    )
  }

  const sql = response?.sql ?? turn?.sql
  const result = message.result ?? response?.result
  return (
    <div className="answer">
      <Summary summary={message.summary} />
      {response?.definitions_used?.length > 0 && (
        <p className="definitions-used muted">
          Followed your {response.definitions_used.length === 1 ? 'definition' : 'definitions'} of{' '}
          {response.definitions_used.map((name) => <span key={name} className="chip chip--accent">{name}</span>)}
        </p>
      )}
      {result ? (
        <ResultPanel result={result} analysis={message.analysis} question={message.question}
          onAnalysis={(next) => onAnalysis(message.id, next)} />
      ) : (
        turn && (
          <div className="restore">
            <p className="muted">
              {turn.has_result
                ? 'The rows saved with this answer can be shown again.'
                : `${turn.row_count != null ? `${turn.row_count.toLocaleString('en-US')} rows` : 'Results'} were not kept. Run the saved query again to see them.`}
            </p>
            <button type="button" className="btn btn--small" disabled={message.restoring} onClick={() => onShowResults(message)}>
              {message.restoring ? 'Running…' : 'Show results'}
            </button>
            {message.restoreError && <p className="inline-error" role="alert">{message.restoreError}</p>}
          </div>
        )
      )}
      <SQLViewer
        sql={sql}
        explanation={response?.explanation ?? turn?.explanation}
        assumptions={response?.assumptions ?? []}
        tablesUsed={response?.tables_used ?? turn?.tables_used ?? []}
        validation={response?.validation ?? (turn ? { valid: true } : null)}
        attempts={response?.attempts ?? []}
      />
    </div>
  )
}
