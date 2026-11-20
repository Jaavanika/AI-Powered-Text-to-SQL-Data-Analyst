import { useState } from 'react'

const KEYWORDS = [
  'SELECT', 'DISTINCT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET', 'JOIN',
  'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN',
  'IS', 'NULL', 'LIKE', 'BETWEEN', 'EXISTS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'WITH', 'UNION', 'ALL',
  'ASC', 'DESC', 'OVER', 'PARTITION BY', 'CAST',
]
// strings | numbers | keywords (longest first so "GROUP BY" wins over "GROUP")
const TOKEN = new RegExp(
  `('(?:[^']|'')*')|(\\b\\d+(?:\\.\\d+)?\\b)|\\b(${[...KEYWORDS].sort((a, b) => b.length - a.length)
    .map((k) => k.replace(' ', '\\s+')).join('|')})\\b`,
  'gi',
)

/** Light syntax colouring, built from React nodes (never raw HTML) because SQL can contain anything. */
export function highlight(sql) {
  const out = []
  let last = 0
  let n = 0
  for (const m of sql.matchAll(TOKEN)) {
    if (m.index > last) out.push(sql.slice(last, m.index))
    const cls = m[1] ? 'sql-str' : m[2] ? 'sql-num' : 'sql-kw'
    out.push(<span key={n++} className={cls}>{m[0]}</span>)
    last = m.index + m[0].length
  }
  if (last < sql.length) out.push(sql.slice(last))
  return out
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

export default function SQLViewer({ sql, explanation, assumptions = [], tablesUsed = [], validation = null,
  attempts = [], defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const [copied, setCopied] = useState(false)
  if (!sql) return null

  const copy = async () => {
    if (await copyText(sql)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <div className="sql-viewer">
      <div className="sql-viewer__head">
        <button type="button" className="disclosure" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span className="disclosure__icon" aria-hidden="true">{open ? '▾' : '▸'}</span>
          How this was answered
        </button>
        <span className="chips">
          {validation?.valid && <span className="chip chip--good" title="Parsed, checked against the schema and run read-only">✓ Validated · read-only</span>}
          {attempts.length > 0 && (
            <span className="chip chip--warn" title="The first SQL was rejected and corrected">
              Corrected {attempts.length}×
            </span>
          )}
          {tablesUsed.map((t) => <span key={t} className="chip">{t}</span>)}
        </span>
      </div>

      {open && (
        <div className="sql-viewer__body">
          {explanation && <p className="sql-viewer__explain">{explanation}</p>}
          {assumptions.length > 0 && (
            <div>
              <div className="label">Assumptions</div>
              <ul className="plain-list">{assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
          <div className="code-block">
            <button type="button" className="btn btn--ghost btn--small code-block__copy" onClick={copy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
            <pre aria-label="Generated SQL"><code>{highlight(sql)}</code></pre>
          </div>
          {attempts.length > 0 && (
            <details className="attempts">
              <summary>{attempts.length} earlier attempt{attempts.length > 1 ? 's were' : ' was'} rejected</summary>
              <ol>
                {attempts.map((a, i) => (
                  <li key={i}>
                    <span className="muted">{a.error}</span>
                    {a.sql && <pre><code>{a.sql}</code></pre>}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
