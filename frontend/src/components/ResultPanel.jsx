import { lazy, Suspense, useMemo, useState } from 'react'
import { api } from '../services/api.js'
import ResultTable from './ResultTable.jsx'

// The charting library is large, so it is only fetched when a chart is first shown.
const ChartViewer = lazy(() => import('./ChartViewer.jsx'))

const TYPE_LABEL = { bar: 'Bar', line: 'Line', pie: 'Pie', scatter: 'Scatter', kpi: 'Figure', table: 'Table' }

/**
 * The result of a query: a chart (with a switcher for the other feasible types) and its table twin.
 * Switching the type asks the API to redraw from the same rows; nothing is re-queried.
 */
export default function ResultPanel({ result, analysis, question, onAnalysis }) {
  const chart = analysis?.chart
  const drawable = chart && chart.type !== 'table' && chart.data?.length > 0
  const [view, setView] = useState(drawable ? 'chart' : 'table')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const labels = useMemo(
    () => Object.fromEntries((analysis?.profile?.columns || []).map((c) => [c.name, c.original_name])),
    [analysis],
  )

  if (!result) return null
  if (result.row_count === 0) {
    return <p className="empty-result">The query ran but returned no rows.</p>
  }

  const options = drawable
    ? [chart.type, ...(chart.alternatives || []).filter((t) => t !== 'table' && t !== chart.type), 'table']
    : []
  const current = view === 'table' ? 'table' : chart?.type

  const select = async (type) => {
    setError(null)
    if (type === 'table') return setView('table')
    if (type === chart.type) return setView('chart')
    setBusy(true)
    try {
      const next = await api.chart({
        columns: result.columns, rows: result.rows, truncated: result.truncated,
        question, chart_type: type,
      })
      onAnalysis?.({ ...analysis, chart: next.chart })
      setView('chart')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="result-panel" aria-label="Result">
      {options.length > 1 && (
        <div className="segmented" role="group" aria-label="View result as">
          {options.map((type) => (
            <button key={type} type="button" className={type === current ? 'is-active' : undefined}
              aria-pressed={type === current} disabled={busy} onClick={() => select(type)}>
              {TYPE_LABEL[type]}
            </button>
          ))}
        </div>
      )}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {view === 'chart' && drawable ? (
        <Suspense fallback={<p className="muted" role="status">Loading chart…</p>}>
          <ChartViewer spec={chart} labels={labels} dimmed={busy} />
        </Suspense>
      ) : (
        <ResultTable columns={result.columns} rows={result.rows} truncated={result.truncated} maxRows={result.max_rows} />
      )}
    </section>
  )
}
