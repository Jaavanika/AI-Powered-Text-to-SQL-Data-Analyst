import { describe, expect, it } from 'vitest'
import { BOX, layoutDiagram, visibleColumns } from './diagramLayout.js'

const col = (name, extra = {}) => ({ name, original_name: name, type: 'integer', db_type: 'INTEGER', nullable: true, is_primary_key: false, ...extra })
const table = (name, columns) => ({ name, original_name: name, row_count: 10, columns, primary_key: [], primary_key_inferred: false, is_junction: false })
const rel = (from, fromCol, to, toCol, extra = {}) => ({
  id: `fk:${from}.${fromCol}->${to}`, from_table: from, from_columns: [fromCol], to_table: to, to_columns: [toCol],
  source: 'declared', cardinality: 'many_to_one', ...extra,
})

// The sample shop: orders is the hub; payments and customers/products hang off it; regions off customers.
const SHOP = {
  tables: [
    table('customers', [col('customer_id', { is_primary_key: true }), col('customer_name'), col('region_id')]),
    table('orders', [col('order_id', { is_primary_key: true }), col('customer_id'), col('product_id'), col('quantity')]),
    table('payments', [col('payment_id', { is_primary_key: true }), col('order_id'), col('amount')]),
    table('products', [col('product_id', { is_primary_key: true }), col('product_name')]),
    table('regions', [col('region_id', { is_primary_key: true }), col('region_name')]),
    table('audit_log', [col('entry'), col('at')]),                // no relationships at all
  ],
  relationships: [
    rel('orders', 'customer_id', 'customers', 'customer_id'), rel('orders', 'product_id', 'products', 'product_id'),
    rel('payments', 'order_id', 'orders', 'order_id'), rel('customers', 'region_id', 'regions', 'region_id'),
  ],
}

const node = (layout, name) => layout.nodes.find((n) => n.name === name)
const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

describe('visibleColumns', () => {
  it('puts key columns first so every line has somewhere to attach', () => {
    const t = table('orders', [col('quantity'), col('note'), col('customer_id'), col('order_id', { is_primary_key: true })])
    const { rows } = visibleColumns(t, [rel('orders', 'customer_id', 'customers', 'customer_id')])
    expect(rows.map((r) => r.name)).toEqual(['customer_id', 'order_id', 'quantity', 'note'])
    expect(rows[0]).toMatchObject({ fk: true, pk: false })
    expect(rows[1]).toMatchObject({ fk: false, pk: true })
  })

  it('marks columns that other tables point at as keys too', () => {
    const t = table('customers', [col('name'), col('customer_id')])
    const { rows } = visibleColumns(t, [rel('orders', 'customer_id', 'customers', 'customer_id')])
    expect(rows[0].name).toBe('customer_id')
  })

  it('caps the number of rows and counts what is left out', () => {
    const t = table('wide', Array.from({ length: 20 }, (_, i) => col(`c${i}`)))
    const { rows, hidden } = visibleColumns(t, [])
    expect(rows).toHaveLength(BOX.maxRows)
    expect(hidden).toBe(12)
  })
})

describe('layoutDiagram', () => {
  const layout = layoutDiagram(SHOP)

  it('draws every table once and every relationship', () => {
    expect(layout.nodes.map((n) => n.name).sort()).toEqual(['audit_log', 'customers', 'orders', 'payments', 'products', 'regions'])
    expect(layout.edges).toHaveLength(4)
    expect(layout.hiddenTables).toBe(0)
  })

  it('never lets two boxes overlap', () => {
    for (const a of layout.nodes) for (const b of layout.nodes) if (a !== b) expect(overlaps(a, b)).toBe(false)
  })

  it('keeps every box inside the drawing', () => {
    for (const n of layout.nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0)
      expect(n.y).toBeGreaterThanOrEqual(0)
      expect(n.x + n.width).toBeLessThanOrEqual(layout.width)
      expect(n.y + n.height).toBeLessThanOrEqual(layout.height)
    }
  })

  it('puts the most connected table first and its neighbours one column further right', () => {
    const hub = node(layout, 'orders')
    expect(hub.x).toBe(BOX.margin)
    for (const name of ['customers', 'products', 'payments']) expect(node(layout, name).x).toBe(hub.x + BOX.width + BOX.gapX)
    expect(node(layout, 'regions').x).toBe(hub.x + 2 * (BOX.width + BOX.gapX))
  })

  it('puts tables with no relationships in a separate grid below the connected ones', () => {
    const lone = node(layout, 'audit_log')
    expect(lone.isolated).toBe(true)
    const connectedBottom = Math.max(...['orders', 'customers', 'products', 'payments', 'regions'].map((n) => node(layout, n).y + node(layout, n).height))
    expect(lone.y).toBeGreaterThan(connectedBottom)
  })

  it('attaches each line to the right row of each box, with the cardinality at each end', () => {
    const e = layout.edges.find((x) => x.id === 'fk:orders.customer_id->customers')
    const orders = node(layout, 'orders')
    const customers = node(layout, 'customers')
    const row = (n, name) => n.y + BOX.header + n.rows.findIndex((r) => r.name === name) * BOX.row + BOX.row / 2
    expect(e.start).toMatchObject({ x: orders.x + orders.width, y: row(orders, 'customer_id'), label: 'N', dir: 1 })
    expect(e.end).toMatchObject({ x: customers.x, y: row(customers, 'customer_id'), label: '1', dir: -1 })
    expect(e.path).toMatch(/^M [\d.]+ [\d.]+ C /)
  })

  it('shows 1 at both ends of a one-to-one relationship', () => {
    const l = layoutDiagram({ tables: [table('a', [col('id')]), table('b', [col('a_id')])],
      relationships: [rel('b', 'a_id', 'a', 'id', { cardinality: 'one_to_one' })] })
    expect(l.edges[0].start.label).toBe('1')
    expect(l.edges[0].end.label).toBe('1')
  })

  it('routes a line round the side when both tables are in the same column', () => {
    // a hub with two children that also reference each other: the children share a column
    const l = layoutDiagram({
      tables: [table('hub', [col('id')]), table('x', [col('hub_id'), col('id')]), table('y', [col('hub_id'), col('x_id')])],
      relationships: [rel('x', 'hub_id', 'hub', 'id'), rel('y', 'hub_id', 'hub', 'id'), rel('y', 'x_id', 'x', 'id')],
    })
    const loop = l.edges.find((e) => e.id === 'fk:y.x_id->x')
    expect(loop.start.dir).toBe(1)
    expect(loop.end.dir).toBe(1)
    const x = l.nodes.find((n) => n.name === 'x')
    expect(l.width).toBeGreaterThan(x.x + x.width + 60)              // room was made for the loop
  })

  it('points at the bottom of a box when the column is not drawn', () => {
    const wide = table('wide', [...Array.from({ length: 12 }, (_, i) => col(`c${i}`)), col('zzz_fk')])
    // zzz_fk is a key, so it is promoted into view; make 9 keys so one must be dropped
    const keys = Array.from({ length: 9 }, (_, i) => col(`k${i}`))
    const l = layoutDiagram({
      tables: [table('child', keys), table('parent', [col('id')])],
      relationships: keys.map((k) => rel('child', k.name, 'parent', 'id', { id: `fk:${k.name}` })),
    })
    const child = l.nodes.find((n) => n.name === 'child')
    expect(child.hiddenColumns).toBe(1)
    const hidden = l.edges.find((e) => e.id === 'fk:k8')
    expect(hidden.start.y).toBeGreaterThan(child.y + BOX.header + (BOX.maxRows - 1) * BOX.row)
    expect(hidden.start.y).toBeLessThanOrEqual(child.y + child.height)
    expect(wide).toBeTruthy()
  })

  it('is deterministic', () => {
    expect(layoutDiagram(SHOP)).toEqual(layoutDiagram({ ...SHOP, tables: [...SHOP.tables].reverse() }))
  })

  it('shows only the best connected tables of a very large schema and says how many were left out', () => {
    const many = Array.from({ length: 40 }, (_, i) => table(`t${String(i).padStart(2, '0')}`, [col('id'), col('parent_id')]))
    const rels = Array.from({ length: 30 }, (_, i) => rel(`t${String(i + 1).padStart(2, '0')}`, 'parent_id', 't00', 'id', { id: `fk${i}` }))
    const l = layoutDiagram({ tables: many, relationships: rels }, { maxTables: 25 })
    expect(l.nodes).toHaveLength(25)
    expect(l.hiddenTables).toBe(15)
    expect(l.nodes.some((n) => n.name === 't00')).toBe(true)         // the hub is always kept
    expect(l.edges.every((e) => l.nodes.some((n) => n.name === e.from) && l.nodes.some((n) => n.name === e.to))).toBe(true)
  })

  it('handles an empty schema and self-references', () => {
    expect(layoutDiagram({ tables: [], relationships: [] }).nodes).toEqual([])
    const l = layoutDiagram({ tables: [table('emp', [col('id'), col('manager_id')])], relationships: [rel('emp', 'manager_id', 'emp', 'id')] })
    expect(l.edges).toEqual([])                                       // a table pointing at itself has no line to draw
    expect(l.nodes).toHaveLength(1)
  })
})
