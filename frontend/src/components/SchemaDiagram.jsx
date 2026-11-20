import { useMemo, useState } from 'react'
import { BOX, layoutDiagram } from '../lib/diagramLayout.js'

const CHAR = 6.8
const fit = (text, room) => {
  const max = Math.max(3, Math.floor(room / CHAR))
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

/**
 * Entity-relationship diagram of a data source. Each table is a box listing its key columns first; a
 * line joins a foreign key (N) to the key it references (1). Hovering or focusing a table lights up
 * its relationships; clicking selects it. The relationships are also listed as text on the page.
 */
export default function SchemaDiagram({ schema, selected, onSelect }) {
  const layout = useMemo(() => layoutDiagram(schema), [schema])
  const [hovered, setHovered] = useState(null)
  const active = hovered ?? selected

  const incident = (edge) => active && (edge.from === active || edge.to === active)
  const related = useMemo(() => {
    const set = new Set(active ? [active] : [])
    for (const e of layout.edges) {
      if (e.from === active) set.add(e.to)
      if (e.to === active) set.add(e.from)
    }
    return set
  }, [layout, active])

  if (!layout.nodes.length) return <p className="muted">There are no tables to draw.</p>

  return (
    <div className="diagram">
      <div className="diagram__scroll" role="group"
        aria-label="Relationship diagram. The same relationships are listed as text below.">
        <svg width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`}
          xmlns="http://www.w3.org/2000/svg" className="diagram__svg">
          <g className="diagram__edges">
            {layout.edges.map((edge) => {
              const lit = incident(edge)
              const dim = active && !lit
              return (
                <g key={edge.id} className={lit ? 'diagram__edge is-lit' : dim ? 'diagram__edge is-dim' : 'diagram__edge'}
                  data-edge={edge.id}>
                  <title>{`${edge.from}.${edge.fromColumns.join(', ')} references ${edge.to}.${edge.toColumns.join(', ')} (${edge.cardinality.replace(/_/g, '-')})`}</title>
                  <path d={edge.path} fill="none" />
                  {[edge.start, edge.end].map((end, i) => (
                    <g key={i}>
                      <circle cx={end.x} cy={end.y} r={3} />
                      <text x={end.x + end.dir * 11} y={end.y - 6} textAnchor="middle" className="diagram__card">{end.label}</text>
                    </g>
                  ))}
                </g>
              )
            })}
          </g>
          <g className="diagram__nodes">
            {layout.nodes.map((node) => {
              const dim = active && !related.has(node.name)
              const classes = ['diagram__node', node.name === selected ? 'is-selected' : '', dim ? 'is-dim' : ''].join(' ').trim()
              return (
                <g key={node.name} className={classes} transform={`translate(${node.x} ${node.y})`}
                  role="button" tabIndex={0} aria-pressed={node.name === selected} data-table={node.name}
                  aria-label={`Table ${node.name}, ${node.rows.length + node.hiddenColumns} columns. Press Enter to select.`}
                  onMouseEnter={() => setHovered(node.name)} onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(node.name)} onBlur={() => setHovered(null)}
                  onClick={() => onSelect?.(node.name)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect?.(node.name) } }}>
                  <rect className="diagram__box" width={node.width} height={node.height} rx={8} />
                  <rect className="diagram__head" width={node.width} height={BOX.header} rx={8} />
                  <rect className="diagram__head-fill" y={BOX.header - 10} width={node.width} height={10} />
                  <text className="diagram__title" x={BOX.pad} y={BOX.header / 2 + 5}>{fit(node.name, node.width - 2 * BOX.pad)}</text>
                  {node.rows.map((row, i) => {
                    const y = BOX.header + i * BOX.row + BOX.row / 2 + 4
                    const tag = row.pk ? 'PK' : row.fk ? 'FK' : ''
                    const typeWidth = row.type.length * CHAR
                    return (
                      <g key={row.name}>
                        {tag && <text className={row.pk ? 'diagram__tag diagram__tag--pk' : 'diagram__tag'} x={BOX.pad} y={y}>{tag}</text>}
                        <text className="diagram__col" x={BOX.pad + 24} y={y}>
                          {fit(row.name, node.width - 2 * BOX.pad - 24 - typeWidth - 6)}
                        </text>
                        <text className="diagram__type" x={node.width - BOX.pad} y={y} textAnchor="end">{row.type}</text>
                      </g>
                    )
                  })}
                  {node.hiddenColumns > 0 && (
                    <text className="diagram__more" x={BOX.pad} y={BOX.header + node.rows.length * BOX.row + BOX.row / 2 + 4}>
                      + {node.hiddenColumns} more column{node.hiddenColumns === 1 ? '' : 's'}
                    </text>
                  )}
                </g>
              )
            })}
          </g>
        </svg>
      </div>
      <p className="muted diagram__legend">
        A line joins a foreign key (<strong>N</strong>) to the key it points at (<strong>1</strong>). Hover or select a table to follow its relationships.
        {layout.hiddenTables > 0 && ` Showing the ${layout.nodes.length} best connected of ${schema.tables.length} tables.`}
      </p>
    </div>
  )
}
