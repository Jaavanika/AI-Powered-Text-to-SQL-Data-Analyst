import { api } from '../services/api.js'

/**
 * The rows of an answer asked earlier, plus the chart analysis for them.
 *
 * If the server kept the rows (HISTORY_RETAIN_ROWS) they are used. Otherwise, or if that fails, the saved SQL is
 * run again, which goes through the same validation and read-only execution as any other query.
 */
export async function loadSavedResult(datasourceId, turn, question) {
  let result = null
  let source = 'rerun'
  if (turn.has_result) {
    try {
      result = await api.keptResult(turn.id)
      source = 'kept'
    } catch {
      /* the rows are gone (retention, clearing); fall back to running the SQL */
    }
  }
  if (!result) ({ result } = await api.execute(datasourceId, turn.sql))
  const { profile, chart } = await api.chart({
    columns: result.columns, rows: result.rows, truncated: result.truncated, question,
  })
  return { result, analysis: { profile, chart }, source }
}
