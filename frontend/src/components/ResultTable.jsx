import { useMemo, useState } from 'react'
import { formatCell, humanize, isNumber, toCsv } from '../lib/format.js'

const PAGE = 100

function download(filename, text) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export default function ResultTable({ columns, rows, truncated = false, maxRows = null, caption }) {
  const [shown, setShown] = useState(PAGE)

  // A column is numeric (right-aligned, tabular figures) when every non-null value is a number.
  const numeric = useMemo(
    () => columns.map((_, i) => {
      const values = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined)
      return values.length > 0 && values.every(isNumber)
    }),
    [columns, rows],
  )

  const visible = rows.slice(0, shown)

  return (
    <div className="result-table">
      <div className="result-table__bar">
        <span className="muted">
          {caption || `${rows.length.toLocaleString('en-US')} ${rows.length === 1 ? 'row' : 'rows'}`}
          {truncated && ` · cut off at the ${maxRows ? maxRows.toLocaleString('en-US') : 'row'} limit, more rows exist`}
        </span>
        <button type="button" className="btn btn--ghost btn--small" onClick={() => download('result.csv', toCsv(columns, rows))}>
          Download CSV
        </button>
      </div>
      <div className="result-table__scroll" tabIndex={0} role="region" aria-label="Query result table">
        <table>
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th key={i} scope="col" className={numeric[i] ? 'num' : undefined} title={c}>
                  {humanize(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, r) => (
              <tr key={r}>
                {row.map((value, i) => (
                  <td key={i} className={[numeric[i] ? 'num' : '', value == null ? 'null' : ''].join(' ').trim() || undefined}>
                    {formatCell(value)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > shown && (
        <button type="button" className="btn btn--ghost btn--small" onClick={() => setShown((s) => s + PAGE)}>
          Show {Math.min(PAGE, rows.length - shown)} more of {(rows.length - shown).toLocaleString('en-US')} remaining
        </button>
      )}
    </div>
  )
}
