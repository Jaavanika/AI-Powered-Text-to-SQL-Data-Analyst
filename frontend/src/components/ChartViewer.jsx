import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, LabelList, Line, Pie, PieChart,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts'
import { formatCompact, formatHeadline, formatNumber, formatPercent, formatPeriod, humanize, isNumber } from '../lib/format.js'

// Marks follow fixed specs: bars at most 24px thick with a 4px rounded data end, 2px lines, 8px
// end-dots with a 2px surface ring, hairline solid gridlines. Text always uses text tokens;
// the series colour only ever appears on the mark itself.
const MAX_BAR = 24
const slot = (i) => `var(--series-${(i % 8) + 1})`
const TICK = { fill: 'var(--text-muted)', fontSize: 12 }
const AXIS = { stroke: 'var(--axis)' }
const short = (v, n = 22) => (String(v).length > n ? `${String(v).slice(0, n - 1)}…` : String(v))
const lowerFirst = (s) => (s && !/^[A-Z]{2,}/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s)

function Legend({ items }) {
  return (
    <ul className="legend" aria-label="Legend">
      {items.map((item) => (
        <li key={item.name}>
          <span className="swatch" style={{ background: item.color }} aria-hidden="true" />
          {item.name}
        </li>
      ))}
    </ul>
  )
}

/** One row per series: the area wash under a line is a second series with the same name and value. */
export function dedupeEntries(payload) {
  const seen = new Set()
  return payload.filter((p) => {
    const key = `${p.dataKey ?? p.name}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function ChartTooltip({ active, payload, label, formatLabel }) {
  if (!active || !payload?.length) return null
  const title = formatLabel ? formatLabel(label ?? payload[0]?.payload?.name) : (label ?? payload[0]?.payload?.name)
  return (
    <div className="chart-tooltip">
      {title !== undefined && title !== '' && <div className="chart-tooltip__title">{title}</div>}
      {dedupeEntries(payload).map((p, i) => (
        <div key={i} className="chart-tooltip__row">
          <span className="swatch" style={{ background: p.color || p.payload?.fill || p.fill }} aria-hidden="true" />
          <span className="chart-tooltip__name">{p.name}</span>
          <span className="chart-tooltip__value">{isNumber(p.value) ? formatNumber(p.value) : p.value ?? '—'}</span>
        </div>
      ))}
    </div>
  )
}

/** Resolve the display label for a (sanitised) column name. */
const makeLabeler = (labels) => (name) => humanize(labels?.[name] || name)

function KpiView({ spec, label }) {
  const hero = spec.data.length === 1
  return (
    <div className="kpi-group">
      {spec.caption && <div className="kpi__caption">{spec.caption}</div>}
      <div className={hero ? 'kpis kpis--hero' : 'kpis'}>
        {spec.data.map((item, i) => (
          <div className="kpi" key={i}>
            <div className="kpi__label">{humanize(item.label)}</div>
            <div className="kpi__value">{formatHeadline(item.value)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function BarView({ spec, label }) {
  const horizontal = spec.horizontal
  const multi = spec.y.length > 1
  const showValues = !multi && spec.data.length <= 12
  const formatX = spec.x_kind === 'time' ? formatPeriod : (v) => short(v)
  const longest = Math.max(...spec.data.map((d) => String(formatX(d[spec.x])).length), 4)
  const rowHeight = multi ? 18 * spec.y.length + 14 : 34
  const height = horizontal ? Math.max(220, spec.data.length * rowHeight + 48) : 320
  const series = spec.y.map((name, i) => ({ name: label(name), key: name, color: slot(i) }))

  return (
    <>
      {multi && <Legend items={series.map((s) => ({ name: s.name, color: s.color }))} />}
      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={spec.data}
          layout={horizontal ? 'vertical' : 'horizontal'}
          margin={{ top: 18, right: showValues && horizontal ? 56 : 12, bottom: 4, left: 4 }}
          barGap={2}
          barCategoryGap="28%"
        >
          <CartesianGrid stroke="var(--grid)" vertical={horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={TICK} axisLine={false} tickLine={false} tickFormatter={formatCompact} />
              <YAxis type="category" dataKey={spec.x} tick={TICK} axisLine={AXIS} tickLine={false}
                width={Math.min(190, Math.max(70, longest * 7))} tickFormatter={formatX} />
            </>
          ) : (
            <>
              <XAxis dataKey={spec.x} tick={TICK} axisLine={AXIS} tickLine={false} tickFormatter={formatX}
                interval="preserveStartEnd" minTickGap={14} />
              <YAxis tick={TICK} axisLine={false} tickLine={false} tickFormatter={formatCompact} width={52} />
            </>
          )}
          <Tooltip
            cursor={{ fill: 'var(--hover)' }}
            content={<ChartTooltip formatLabel={spec.x_kind === 'time' ? formatPeriod : undefined} />}
          />
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} maxBarSize={MAX_BAR}
              radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false}>
              {showValues && (
                <LabelList dataKey={s.key} position={horizontal ? 'right' : 'top'} formatter={formatCompact}
                  fill="var(--text-secondary)" fontSize={12} />
              )}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </>
  )
}

function LineView({ spec, label }) {
  const single = spec.y.length === 1
  const last = spec.data.length - 1
  const series = spec.y.map((name, i) => ({ name: label(name), key: name, color: slot(i) }))

  // An 8px end marker with a 2px surface ring; the single-series line also gets its closing value.
  const endDot = (color) => function EndDot({ cx, cy, index, value }) {
    if (index !== last || cx == null || cy == null) return <g key={`d${index}`} />
    return (
      <g key={`d${index}`}>
        <circle cx={cx} cy={cy} r={6} fill="var(--surface)" />
        <circle cx={cx} cy={cy} r={4} fill={color} />
        {single && isNumber(value) && (
          <text x={cx - 10} y={cy - 12} textAnchor="end" fontSize={12} fill="var(--text-secondary)">
            {formatCompact(value)}
          </text>
        )}
      </g>
    )
  }

  return (
    <>
      {!single && <Legend items={series.map((s) => ({ name: s.name, color: s.color }))} />}
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={spec.data} margin={{ top: 22, right: 18, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={spec.x} tick={TICK} axisLine={AXIS} tickLine={false}
            tickFormatter={spec.x_kind === 'time' ? formatPeriod : undefined} interval="preserveStartEnd" minTickGap={24} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} tickFormatter={formatCompact} width={52} />
          <Tooltip cursor={{ stroke: 'var(--axis)' }}
            content={<ChartTooltip formatLabel={spec.x_kind === 'time' ? formatPeriod : undefined} />} />
          {single && (
            <Area dataKey={series[0].key} name={series[0].name} stroke="none" fill={series[0].color}
              fillOpacity={0.1} isAnimationActive={false} legendType="none" tooltipType="none" />
          )}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} type="monotone"
              strokeLinecap="round" strokeLinejoin="round" connectNulls dot={endDot(s.color)}
              activeDot={{ r: 5, fill: s.color, stroke: 'var(--surface)', strokeWidth: 2 }} isAnimationActive={false} />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </>
  )
}

function PieView({ spec, label }) {
  const y = spec.y[0]
  const slices = spec.data
    .filter((d) => isNumber(d[y]) && d[y] > 0)
    .map((d, i) => ({ name: String(d[spec.x]), value: d[y], fill: slot(i) }))
  const total = slices.reduce((sum, s) => sum + s.value, 0)

  return (
    <div className="pie">
      <div className="pie__chart">
        <ResponsiveContainer width="100%" height={240}>
          <PieChart>
            <Pie data={slices} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="92%" paddingAngle={2}
              stroke="var(--surface)" strokeWidth={2} isAnimationActive={false} />
            <Tooltip content={<ChartTooltip />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <table className="pie__legend" aria-label={`${label(y)} by ${lowerFirst(label(spec.x))}`}>
        <tbody>
          {slices.map((s) => (
            <tr key={s.name}>
              <td><span className="swatch" style={{ background: s.fill }} aria-hidden="true" /></td>
              <th scope="row">{s.name}</th>
              <td className="num">{formatNumber(s.value)}</td>
              <td className="num muted">{formatPercent(s.value, total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ScatterView({ spec, label }) {
  const x = spec.x
  const y = spec.y[0]
  const labelKey = Object.keys(spec.data[0] || {}).find((k) => k !== x && k !== y)
  const dot = ({ cx, cy }) => (
    <circle cx={cx} cy={cy} r={5} fill={slot(0)} stroke="var(--surface)" strokeWidth={2} />
  )
  const tooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null
    const p = payload[0].payload
    return (
      <div className="chart-tooltip">
        {labelKey && <div className="chart-tooltip__title">{String(p[labelKey])}</div>}
        <div className="chart-tooltip__row"><span className="chart-tooltip__name">{label(x)}</span>
          <span className="chart-tooltip__value">{formatNumber(p[x])}</span></div>
        <div className="chart-tooltip__row"><span className="chart-tooltip__name">{label(y)}</span>
          <span className="chart-tooltip__value">{formatNumber(p[y])}</span></div>
      </div>
    )
  }
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ScatterChart margin={{ top: 18, right: 18, bottom: 22, left: 4 }}>
        <CartesianGrid stroke="var(--grid)" />
        <XAxis type="number" dataKey={x} name={label(x)} tick={TICK} axisLine={AXIS} tickLine={false}
          tickFormatter={formatCompact} label={{ value: label(x), position: 'insideBottom', offset: -12, fill: 'var(--text-secondary)', fontSize: 12 }} />
        <YAxis type="number" dataKey={y} name={label(y)} tick={TICK} axisLine={false} tickLine={false}
          tickFormatter={formatCompact} width={56} />
        <Tooltip cursor={{ stroke: 'var(--axis)' }} content={tooltip} />
        <Scatter data={spec.data} shape={dot} isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

const KIND_NAME = { bar: 'Bar chart', line: 'Line chart', pie: 'Pie chart', scatter: 'Scatter plot', kpi: 'Key figure' }

/**
 * Draws a chart spec from the API. `labels` maps the API's column names to the original headers.
 * The table view is the accessible twin of every chart, so the figure points to it.
 */
export default function ChartViewer({ spec, labels = {}, dimmed = false }) {
  if (!spec || spec.type === 'table' || !spec.data?.length) return null
  const label = makeLabeler(labels)
  const yText = spec.y?.map(label).join(', ')
  const description = spec.type === 'kpi'
    ? `Key figure: ${spec.data.map((d) => `${humanize(d.label)} ${formatHeadline(d.value)}`).join(', ')}.`
    : `${KIND_NAME[spec.type]} of ${yText}${spec.x ? ` by ${lowerFirst(label(spec.x))}` : ''}, ${spec.data.length} points. Use the table view for exact values.`

  const View = { kpi: KpiView, bar: BarView, line: LineView, pie: PieView, scatter: ScatterView }[spec.type]
  return (
    <figure className={dimmed ? 'chart is-dimmed' : 'chart'} aria-label={description}>
      {spec.type !== 'kpi' && spec.type !== 'pie' && spec.y?.length === 1 && (
        <figcaption className="chart__caption">{label(spec.y[0])}{spec.x ? ` by ${lowerFirst(label(spec.x))}` : ''}</figcaption>
      )}
      <View spec={spec} label={label} />
      {spec.notes?.length > 0 && (
        <ul className="chart__notes">{spec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      )}
    </figure>
  )
}
