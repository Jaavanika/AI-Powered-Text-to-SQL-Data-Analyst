// Layout for the relationship diagram. Pure functions: tables and relationships in, boxes and
// routed edges out, so the geometry can be tested without a browser.
//
// Tables that are connected are arranged in columns by their distance from the most connected table
// (the one in the middle of a typical star: orders). Inside a column they are ordered by the average
// position of their neighbours, which keeps most lines from crossing. Tables with no relationships
// go in a grid underneath.

export const BOX = {
  width: 224, header: 32, row: 22, pad: 10, gapX: 120, gapY: 28, maxRows: 8, margin: 20, isolatedColumns: 3,
}

/** The columns to show for a table: keys first, so every line has somewhere to attach, then the rest. */
export function visibleColumns(table, relationships) {
  const foreign = new Set()
  const referenced = new Set()
  for (const r of relationships) {
    if (r.from_table === table.name) r.from_columns.forEach((c) => foreign.add(c))
    if (r.to_table === table.name) r.to_columns.forEach((c) => referenced.add(c))
  }
  const isKey = (c) => c.is_primary_key || foreign.has(c.name) || referenced.has(c.name)
  const keys = table.columns.filter(isKey)
  const others = table.columns.filter((c) => !isKey(c))
  const ordered = [...keys, ...others]
  const rows = ordered.slice(0, BOX.maxRows).map((c) => ({
    name: c.name, type: c.type, pk: !!c.is_primary_key, fk: foreign.has(c.name),
  }))
  return { rows, hidden: Math.max(0, ordered.length - rows.length) }
}

function boxHeight(rows, hidden) {
  return BOX.header + (rows.length + (hidden ? 1 : 0)) * BOX.row + BOX.pad
}

/** Which tables to draw. Large schemas show the best connected ones, and say how many were left out. */
function chooseTables(tables, relationships, maxTables) {
  const degree = new Map(tables.map((t) => [t.name, 0]))
  for (const r of relationships) {
    degree.set(r.from_table, (degree.get(r.from_table) ?? 0) + 1)
    degree.set(r.to_table, (degree.get(r.to_table) ?? 0) + 1)
  }
  if (tables.length <= maxTables) return { shown: tables, degree, hidden: 0 }
  const ranked = [...tables].sort((a, b) => degree.get(b.name) - degree.get(a.name) || a.name.localeCompare(b.name))
  const shown = ranked.slice(0, maxTables)
  return { shown, degree, hidden: tables.length - shown.length }
}

export function layoutDiagram(schema, { maxTables = 30 } = {}) {
  const allRelationships = schema.relationships.filter((r) => r.from_table !== r.to_table)
  const { shown, degree, hidden } = chooseTables(schema.tables, allRelationships, maxTables)
  const names = new Set(shown.map((t) => t.name))
  const relationships = allRelationships.filter((r) => names.has(r.from_table) && names.has(r.to_table))
  const byName = new Map(shown.map((t) => [t.name, t]))

  const neighbours = new Map(shown.map((t) => [t.name, new Set()]))
  for (const r of relationships) {
    neighbours.get(r.from_table).add(r.to_table)
    neighbours.get(r.to_table).add(r.from_table)
  }

  // Components, each rooted at its best connected table, in a stable order.
  const order = [...shown].sort((a, b) => degree.get(b.name) - degree.get(a.name) || a.name.localeCompare(b.name))
  const seen = new Set()
  const components = []
  for (const start of order) {
    if (seen.has(start.name)) continue
    const layers = [[start.name]]
    seen.add(start.name)
    for (let depth = 0; depth < layers.length; depth++) {
      const next = []
      for (const name of layers[depth]) {
        for (const other of [...neighbours.get(name)].sort()) {
          if (!seen.has(other)) { seen.add(other); next.push(other) }
        }
      }
      if (next.length) layers.push(next)
    }
    components.push(layers)
  }

  const connected = components.filter((layers) => layers.flat().length > 1)
  const isolated = components.filter((layers) => layers.flat().length === 1).map((layers) => layers[0][0]).sort()

  const nodes = new Map()
  const makeNode = (name, x, y, extra = {}) => {
    const { rows, hidden: hiddenColumns } = visibleColumns(byName.get(name), relationships)
    const node = { name, x, y, width: BOX.width, rows, hiddenColumns, height: boxHeight(rows, hiddenColumns), ...extra }
    nodes.set(name, node)
    return node
  }

  let cursorY = BOX.margin
  let maxX = BOX.margin
  for (const layers of connected) {
    // order each layer by the mean row of its neighbours in the layer before it
    const position = new Map()
    layers.forEach((layer, depth) => {
      if (depth > 0) {
        const mean = (n) => {
          const placed = [...neighbours.get(n)].filter((o) => position.has(o))
          return placed.length ? placed.reduce((s, o) => s + position.get(o), 0) / placed.length : 0
        }
        layer.sort((a, b) => mean(a) - mean(b) || a.localeCompare(b))
      }
      layer.forEach((n, i) => position.set(n, i))
    })

    let componentHeight = 0
    layers.forEach((layer, depth) => {
      let y = cursorY
      for (const name of layer) {
        const node = makeNode(name, BOX.margin + depth * (BOX.width + BOX.gapX), y)
        y += node.height + BOX.gapY
        maxX = Math.max(maxX, node.x + node.width)
      }
      componentHeight = Math.max(componentHeight, y - cursorY)
    })
    cursorY += componentHeight + BOX.gapY
  }

  if (isolated.length) {
    const columns = Math.min(BOX.isolatedColumns, isolated.length)
    const heights = []
    isolated.forEach((name, i) => {
      const col = i % columns
      const rowIndex = Math.floor(i / columns)
      const rowTop = cursorY + heights.slice(0, rowIndex).reduce((s, h) => s + h + BOX.gapY, 0)
      const node = makeNode(name, BOX.margin + col * (BOX.width + BOX.gapX), rowTop, { isolated: true })
      heights[rowIndex] = Math.max(heights[rowIndex] ?? 0, node.height)
      maxX = Math.max(maxX, node.x + node.width)
    })
    cursorY += heights.reduce((s, h) => s + h + BOX.gapY, 0)
  }

  const anchorY = (node, column) => {
    const index = node.rows.findIndex((r) => r.name === column)
    return index === -1
      ? node.y + node.height - BOX.pad / 2                       // a column that is not drawn: the bottom edge
      : node.y + BOX.header + index * BOX.row + BOX.row / 2
  }

  const edges = relationships.map((r) => {
    const child = nodes.get(r.from_table)
    const parent = nodes.get(r.to_table)
    const y1 = anchorY(child, r.from_columns[0])
    const y2 = anchorY(parent, r.to_columns[0])
    let x1, x2, d1, d2, path
    if (child.x + child.width + BOX.gapX / 2 <= parent.x) {                // parent to the right
      x1 = child.x + child.width; x2 = parent.x; d1 = 1; d2 = -1
      const dx = Math.max(40, (x2 - x1) / 2)
      path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
    } else if (parent.x + parent.width + BOX.gapX / 2 <= child.x) {        // parent to the left
      x1 = child.x; x2 = parent.x + parent.width; d1 = -1; d2 = 1
      const dx = Math.max(40, (x1 - x2) / 2)
      path = `M ${x1} ${y1} C ${x1 - dx} ${y1}, ${x2 + dx} ${y2}, ${x2} ${y2}`
    } else {                                                               // same column: loop round the right side
      x1 = child.x + child.width; x2 = parent.x + parent.width; d1 = 1; d2 = 1
      path = `M ${x1} ${y1} C ${x1 + 70} ${y1}, ${x2 + 70} ${y2}, ${x2} ${y2}`
      maxX = Math.max(maxX, x1 + 70)
    }
    const many = r.cardinality === 'many_to_one'
    return {
      id: r.id, from: r.from_table, to: r.to_table, path, source: r.source, cardinality: r.cardinality,
      fromColumns: r.from_columns, toColumns: r.to_columns,
      // the small "N" / "1" at each end: many on the foreign-key side, one on the key side
      start: { x: x1, y: y1, dir: d1, label: many ? 'N' : '1' },
      end: { x: x2, y: y2, dir: d2, label: '1' },
    }
  })

  return {
    nodes: [...nodes.values()], edges,
    width: maxX + BOX.margin, height: Math.max(cursorY, BOX.margin * 2),
    hiddenTables: hidden,
  }
}
