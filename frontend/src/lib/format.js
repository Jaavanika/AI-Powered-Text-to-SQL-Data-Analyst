const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const full = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })

export const isNumber = (v) => typeof v === 'number' && Number.isFinite(v)

/** A number as it should read in a cell or tooltip: grouped digits, at most 2 decimals. */
export function formatNumber(value) {
  return isNumber(value) ? full.format(value) : String(value)
}

/** Axis ticks and tiles: compact from 1,000 up (0 / 1K / 2K), full below. */
export function formatCompact(value) {
  if (!isNumber(value)) return String(value)
  return Math.abs(value) >= 1000 ? compact.format(value) : full.format(value)
}

/** Headline figures: the full value up to a million, compact above, so tiles stay short. */
export function formatHeadline(value) {
  if (!isNumber(value)) return value == null ? '—' : String(value)
  return Math.abs(value) >= 1_000_000 ? compact.format(value) : full.format(value)
}

export function formatCell(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (isNumber(value)) return full.format(value)
  return String(value)
}

/** 'total_revenue' / 'Total Revenue' / 'COUNT(*)' -> 'Total revenue' */
export function humanize(name) {
  if (!name) return ''
  const text = String(name).replace(/[_\s]+/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase().replace(/\bid\b/g, 'ID')
}

/** Time-axis labels: '2024-03' -> 'Mar 2024', '2024-03-05' -> '5 Mar 2024'. Anything else is left alone. */
export function formatPeriod(value) {
  if (typeof value !== 'string') return value
  let m = value.match(/^(\d{4})-(\d{2})$/)
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${MONTHS[+m[2] - 1]} ${m[1]}`
  m = value.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`
  return value
}

/** Share of a total, for legends: 0.2834 of 1 -> '28%'. */
export function formatPercent(part, total) {
  if (!total) return ''
  const pct = (part / total) * 100
  return `${pct < 10 && pct > 0 ? pct.toFixed(1) : Math.round(pct)}%`
}

export function formatDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** CSV with every field quoted when needed, so commas, quotes and newlines in data survive. */
export function toCsv(columns, rows) {
  const cell = (v) => {
    if (v === null || v === undefined) return ''
    const s = String(v)
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [columns, ...rows].map((row) => row.map(cell).join(',')).join('\r\n')
}
