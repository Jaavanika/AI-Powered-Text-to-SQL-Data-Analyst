import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import SchemaDiagram from './SchemaDiagram.jsx'

const col = (name, extra = {}) => ({ name, original_name: name, type: 'integer', db_type: 'INTEGER', nullable: true, is_primary_key: false, ...extra })
const table = (name, columns) => ({ name, original_name: name, row_count: 10, columns, primary_key: [], primary_key_inferred: false, is_junction: false })
const rel = (from, fromCol, to, toCol, extra = {}) => ({
  id: `fk:${from}.${fromCol}->${to}`, from_table: from, from_columns: [fromCol], to_table: to, to_columns: [toCol],
  source: 'declared', cardinality: 'many_to_one', ...extra,
})

const SCHEMA = {
  tables: [
    table('customers', [col('customer_id', { is_primary_key: true }), col('customer_name', { type: 'text' })]),
    table('orders', [col('order_id', { is_primary_key: true }), col('customer_id'), col('product_id')]),
    table('products', [col('product_id', { is_primary_key: true }), col('product_name', { type: 'text' })]),
    table('audit_log', [col('entry', { type: 'text' })]),
  ],
  relationships: [rel('orders', 'customer_id', 'customers', 'customer_id'), rel('orders', 'product_id', 'products', 'product_id')],
}

const nodeOf = (container, name) => container.querySelector(`[data-table="${name}"]`)
const edgeOf = (container, id) => container.querySelector(`[data-edge="${id}"]`)

describe('SchemaDiagram', () => {
  it('draws a box for every table with its columns, types and key markers', () => {
    const { container } = render(<SchemaDiagram schema={SCHEMA} selected={null} />)
    expect(container.querySelectorAll('[data-table]')).toHaveLength(4)
    const orders = nodeOf(container, 'orders')
    expect(within(orders).getByText('orders')).toBeInTheDocument()
    expect(within(orders).getByText('order_id')).toBeInTheDocument()
    expect(within(orders).getAllByText('PK')).toHaveLength(1)
    expect(within(orders).getAllByText('FK')).toHaveLength(2)
    expect(within(nodeOf(container, 'customers')).getByText('text')).toBeInTheDocument()
  })

  it('draws a line for each relationship, labelled many at the foreign key and one at the key', () => {
    const { container } = render(<SchemaDiagram schema={SCHEMA} selected={null} />)
    expect(container.querySelectorAll('[data-edge]')).toHaveLength(2)
    const edge = edgeOf(container, 'fk:orders.customer_id->customers')
    expect(within(edge).getByText('N')).toBeInTheDocument()
    expect(within(edge).getByText('1')).toBeInTheDocument()
    expect(edge.querySelector('title')).toHaveTextContent('orders.customer_id references customers.customer_id (many-to-one)')
    expect(edge.querySelector('path').getAttribute('d')).toMatch(/^M /)
  })

  it('is described for assistive technology and gives every table a keyboard-operable name', () => {
    render(<SchemaDiagram schema={SCHEMA} selected={null} />)
    expect(screen.getByRole('group', { name: /same relationships are listed as text below/ })).toBeInTheDocument()
    const orders = screen.getByRole('button', { name: 'Table orders, 3 columns. Press Enter to select.' })
    expect(orders).toHaveAttribute('tabindex', '0')
    expect(orders).toHaveAttribute('aria-pressed', 'false')
  })

  it('selects a table on click, Enter or Space', async () => {
    const onSelect = vi.fn()
    render(<SchemaDiagram schema={SCHEMA} selected={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /^Table orders/ }))
    expect(onSelect).toHaveBeenLastCalledWith('orders')
    screen.getByRole('button', { name: /^Table customers/ }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenLastCalledWith('customers')
    await userEvent.keyboard(' ')
    expect(onSelect).toHaveBeenCalledTimes(3)
  })

  it('marks the selected table', () => {
    render(<SchemaDiagram schema={SCHEMA} selected="products" />)
    expect(screen.getByRole('button', { name: /^Table products/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^Table orders/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('lights up a table\'s relationships and dims the rest on hover, and restores them after', () => {
    const { container } = render(<SchemaDiagram schema={SCHEMA} selected={null} />)
    const toCustomers = edgeOf(container, 'fk:orders.customer_id->customers')
    const toProducts = edgeOf(container, 'fk:orders.product_id->products')
    expect(toCustomers).not.toHaveClass('is-lit')

    fireEvent.mouseEnter(nodeOf(container, 'customers'))
    expect(toCustomers).toHaveClass('is-lit')
    expect(toProducts).toHaveClass('is-dim')
    expect(nodeOf(container, 'orders')).not.toHaveClass('is-dim')           // a neighbour stays bright
    expect(nodeOf(container, 'products')).toHaveClass('is-dim')
    expect(nodeOf(container, 'audit_log')).toHaveClass('is-dim')

    fireEvent.mouseLeave(nodeOf(container, 'customers'))
    expect(toCustomers).not.toHaveClass('is-lit')
    expect(nodeOf(container, 'audit_log')).not.toHaveClass('is-dim')
  })

  it('does the same for keyboard focus, and follows the selection when nothing is hovered', () => {
    const { container } = render(<SchemaDiagram schema={SCHEMA} selected="products" />)
    expect(edgeOf(container, 'fk:orders.product_id->products')).toHaveClass('is-lit')       // from the selection
    fireEvent.focus(nodeOf(container, 'customers'))
    expect(edgeOf(container, 'fk:orders.customer_id->customers')).toHaveClass('is-lit')     // focus wins over selection
    expect(edgeOf(container, 'fk:orders.product_id->products')).toHaveClass('is-dim')
    fireEvent.blur(nodeOf(container, 'customers'))
    expect(edgeOf(container, 'fk:orders.product_id->products')).toHaveClass('is-lit')
  })

  it('truncates names that do not fit rather than overflowing the box', () => {
    const schema = { tables: [table('a_table_with_an_extraordinarily_long_name_indeed', [col('a_column_name_that_is_far_too_long_to_fit_here')])], relationships: [] }
    render(<SchemaDiagram schema={schema} selected={null} />)
    expect(screen.getByText(/^a_table_with_an_extraordinari.*…$/)).toBeInTheDocument()
    expect(screen.getByText(/^a_column_name.*…$/)).toBeInTheDocument()
  })

  it('says how many columns are not drawn', () => {
    const schema = { tables: [table('wide', Array.from({ length: 12 }, (_, i) => col(`c${i}`)))], relationships: [] }
    render(<SchemaDiagram schema={schema} selected={null} />)
    expect(screen.getByText('+ 4 more columns')).toBeInTheDocument()
  })

  it('explains how to read it, and notes when tables were left out', () => {
    render(<SchemaDiagram schema={SCHEMA} selected={null} />)
    expect(screen.getByText(/joins a foreign key/)).toBeInTheDocument()
    const many = { tables: Array.from({ length: 40 }, (_, i) => table(`t${i}`, [col('id')])), relationships: [] }
    render(<SchemaDiagram schema={many} selected={null} />)
    expect(screen.getByText(/Showing the 30 best connected of 40 tables/)).toBeInTheDocument()
  })

  it('copes with a schema that has no tables', () => {
    render(<SchemaDiagram schema={{ tables: [], relationships: [] }} selected={null} />)
    expect(screen.getByText('There are no tables to draw.')).toBeInTheDocument()
  })
})
