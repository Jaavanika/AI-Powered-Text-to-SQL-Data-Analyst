import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi, uploadSource } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))

import SchemaExplorer from './SchemaExplorer.jsx'

const col = (name, type = 'text', extra = {}) => ({ name, original_name: name, type, db_type: type.toUpperCase(), nullable: true, is_primary_key: false, ...extra })

const schema = (relationships = []) => ({
  datasource_id: 'up1', dialect: 'sqlite', many_to_many: [], relationships,
  tables: [
    { name: 'customers', original_name: 'customers', row_count: 12, primary_key: ['customer_id'], primary_key_inferred: true, is_junction: false,
      columns: [col('customer_id', 'integer', { original_name: 'Customer ID', is_primary_key: true }), col('city')] },
    { name: 'orders', original_name: 'orders', row_count: 60, primary_key: ['order_id'], primary_key_inferred: true, is_junction: false,
      columns: [col('order_id', 'integer', { is_primary_key: true }), col('customer_id', 'integer')] },
  ],
})
const userRel = { id: 'user:abc', from_table: 'orders', from_columns: ['customer_id'], to_table: 'customers', to_columns: ['customer_id'], source: 'user', cardinality: 'many_to_one' }
const declared = { ...userRel, id: 'fk:orders.customer_id->customers', source: 'declared' }
const suggestion = { from_table: 'orders', from_column: 'customer_id', to_table: 'customers', to_column: 'customer_id',
  cardinality: 'many_to_one', match_ratio: 1, reason: 'Column names match, 100% of the 12 distinct values exist.' }

const navigate = vi.fn()
const open = (source = uploadSource) => {
  localStorage.setItem('tsql.datasource', source.id)
  return renderInApp(<SchemaExplorer navigate={navigate} />)
}

beforeEach(() => {
  Object.assign(api, mockApi())
  api.getSchema.mockResolvedValue(schema())
  api.suggestRelationships.mockResolvedValue([suggestion])
  api.previewTable.mockResolvedValue({ table: 'customers', row_count: 12, columns: [{ name: 'customer_id' }, { name: 'city' }],
    rows: [{ customer_id: 1, city: 'Oslo' }] })
})

describe('tables and columns', () => {
  it('lists the tables and describes the selected one', async () => {
    open()
    expect(await screen.findByRole('heading', { name: 'customers' })).toBeInTheDocument()
    expect(screen.getByText(/12 rows · key: customer_id \(inferred\)/)).toBeInTheDocument()
    const row = screen.getByRole('row', { name: /customer_id Customer ID integer primary/ })
    expect(row).toBeInTheDocument()
    expect(await screen.findByText('Oslo')).toBeInTheDocument()                  // sample rows
    expect(api.previewTable).toHaveBeenCalledWith('up1', 'customers', 10)
  })

  it('switches tables', async () => {
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(screen.getByRole('button', { name: /^orders/ }))        // the list entry, not the diagram box
    expect(await screen.findByRole('heading', { name: 'orders' })).toBeInTheDocument()
    expect(api.previewTable).toHaveBeenLastCalledWith('up1', 'orders', 10)
  })

  it('shows foreign keys on the columns that have them', async () => {
    api.getSchema.mockResolvedValue(schema([declared]))
    open()
    await userEvent.click(await screen.findByRole('button', { name: /^orders/ }))
    expect(await screen.findByText('→ customers.customer_id')).toBeInTheDocument()
  })
})

describe('relationships', () => {
  it('warns that unrelated uploaded tables cannot be queried together and offers suggestions', async () => {
    open()
    expect(await screen.findByText(/no relationships yet/)).toBeInTheDocument()
    const section = screen.getByRole('region', { name: 'Relationships' }) || screen.getByLabelText('Relationships')
    expect(section).toBeTruthy()
    expect(screen.getByText('Suggested relationships')).toBeInTheDocument()
    expect(screen.getByText(/100% of values match/)).toBeInTheDocument()
    expect(screen.getByText(/Column names match, 100% of the 12 distinct values exist/)).toBeInTheDocument()
  })

  it('confirms a suggestion and refreshes', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.confirmRelationship).toHaveBeenCalledWith('up1', {
      from_table: 'orders', from_column: 'customer_id', to_table: 'customers', to_column: 'customer_id' }))
    await waitFor(() => expect(api.getSchema).toHaveBeenCalledTimes(2))            // reloaded after confirming
  })

  it('lists confirmed relationships and lets only those be removed', async () => {
    api.getSchema.mockResolvedValue(schema([declared, { ...userRel, id: 'user:xyz', from_table: 'orders', from_columns: ['order_id'] }]))
    api.suggestRelationships.mockResolvedValue([])
    open()
    await screen.findByText('declared in database')
    expect(screen.getByText('confirmed by you')).toBeInTheDocument()
    const removes = screen.getAllByRole('button', { name: 'Remove' })
    expect(removes).toHaveLength(1)                                                // declared keys have no Remove
    await userEvent.click(removes[0])
    await waitFor(() => expect(api.deleteRelationship).toHaveBeenCalledWith('up1', 'user:xyz'))
  })

  it('shows an error when confirming fails', async () => {
    api.confirmRelationship.mockRejectedValue(new Error('customers.customer_id must contain unique, non-null values.'))
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('must contain unique')
  })

  it('mentions many-to-many links through link tables', async () => {
    api.getSchema.mockResolvedValue({ ...schema([declared]), many_to_many: [{ table_a: 'courses', table_b: 'students', via: 'enrollments' }] })
    open()
    expect(await screen.findByText(/courses ↔ students via enrollments/)).toBeInTheDocument()
  })
})

describe('relationship diagram', () => {
  it('is shown for a schema with several tables and can be hidden and shown again', async () => {
    api.getSchema.mockResolvedValue(schema([declared]))
    open()
    expect(await screen.findByRole('group', { name: /Relationship diagram/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Table customers/ })).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Hide diagram' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await userEvent.click(toggle)
    expect(screen.queryByRole('group', { name: /Relationship diagram/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show diagram' }))
    expect(screen.getByRole('group', { name: /Relationship diagram/ })).toBeInTheDocument()
  })

  it('selects the table you click, and the table detail follows', async () => {
    api.getSchema.mockResolvedValue(schema([declared]))
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(screen.getByRole('button', { name: /^Table orders/ }))
    expect(await screen.findByRole('heading', { name: 'orders' })).toBeInTheDocument()
    expect(api.previewTable).toHaveBeenLastCalledWith('up1', 'orders', 10)
  })

  it('is not offered for a single table', async () => {
    api.getSchema.mockResolvedValue({ ...schema(), tables: schema().tables.slice(0, 1) })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    expect(screen.queryByRole('heading', { name: 'Relationship diagram' })).not.toBeInTheDocument()
  })
})

describe('the sample database', () => {
  it('is read-only: no suggestions, no warning, no way to remove its keys', async () => {
    api.getSchema.mockResolvedValue(schema([declared]))
    api.suggestRelationships.mockResolvedValue([suggestion])
    open({ id: 'sample' })
    await screen.findByText('declared in database')
    expect(screen.queryByText('Suggested relationships')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
  })
})

describe('errors and empty states', () => {
  it('reports a schema that cannot be loaded', async () => {
    api.getSchema.mockRejectedValue(new Error('Data source not found.'))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Data source not found.')
  })
})

describe('blocking columns', () => {
  const box = (name) => screen.getByRole('checkbox', { name })

  it('shows what is already blocked, ticked', async () => {
    api.getBlockedColumns.mockResolvedValue({ columns: ['customers.city'], locked: [] })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    expect(box('Block customers.city')).toBeChecked()
    expect(box('Block customers.customer_id')).not.toBeChecked()
    expect(api.getBlockedColumns).toHaveBeenCalledWith('up1')
  })

  it('blocks a column straight away, keeping what was blocked before', async () => {
    api.getBlockedColumns.mockResolvedValue({ columns: ['orders.order_id'], locked: [] })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(box('Block customers.city'))
    await waitFor(() => expect(api.setBlockedColumns).toHaveBeenCalledWith('up1', expect.arrayContaining(['orders.order_id', 'customers.city'])))
    expect(api.setBlockedColumns.mock.calls[0][1]).toHaveLength(2)
    await waitFor(() => expect(box('Block customers.city')).toBeChecked())
  })

  it('ticks the box at once, before the server has answered, and keeps it if the save succeeds', async () => {
    let finish
    api.setBlockedColumns.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(box('Block customers.city'))
    expect(box('Block customers.city')).toBeChecked()                        // not waiting on the network
    finish({ columns: ['customers.city'], locked: [] })
    await waitFor(() => expect(box('Block customers.city')).toBeChecked())
  })

  it('does not read the preview again until the change is saved, so it cannot show a column that is being blocked', async () => {
    let finish
    api.setBlockedColumns.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    open()
    await screen.findByText('Oslo')
    const before = api.previewTable.mock.calls.length
    await userEvent.click(box('Block customers.city'))
    await new Promise((r) => setTimeout(r, 50))
    expect(api.previewTable.mock.calls.length).toBe(before)                  // still the old list on the server
    finish({ columns: ['customers.city'], locked: [] })
    await waitFor(() => expect(api.previewTable.mock.calls.length).toBeGreaterThan(before))
  })

  it('unblocks a column', async () => {
    api.getBlockedColumns.mockResolvedValue({ columns: ['customers.city'], locked: [] })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(box('Block customers.city'))
    await waitFor(() => expect(api.setBlockedColumns).toHaveBeenCalledWith('up1', []))
    await waitFor(() => expect(box('Block customers.city')).not.toBeChecked())
  })

  it('blocks a whole table, which ticks and locks its columns', async () => {
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(screen.getByRole('checkbox', { name: /Block this whole table/ }))
    await waitFor(() => expect(api.setBlockedColumns).toHaveBeenCalledWith('up1', ['customers.*']))
    await waitFor(() => expect(box('Block customers.city')).toBeChecked())
    expect(box('Block customers.city')).toBeDisabled()
  })

  it('shows the administrator\'s blocks as ticked and not changeable', async () => {
    api.getBlockedColumns.mockResolvedValue({ columns: [], locked: ['customers.city', 'orders.*'] })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    expect(box('Block customers.city')).toBeChecked()
    expect(box('Block customers.city')).toBeDisabled()
    expect(box('Block customers.customer_id')).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: /^orders/ }))
    expect(await screen.findByRole('checkbox', { name: /Block this whole table \(set by your administrator\)/ })).toBeDisabled()
  })

  it('reads the preview again after a change, and says so when the table is blocked', async () => {
    open()
    await screen.findByText('Oslo')
    const before = api.previewTable.mock.calls.length
    api.previewTable.mockRejectedValue(new Error('This table is blocked, so it cannot be previewed.'))
    await userEvent.click(screen.getByRole('checkbox', { name: /Block this whole table/ }))
    expect(await screen.findByText('This table is blocked, so no rows are shown.')).toBeInTheDocument()
    expect(api.previewTable.mock.calls.length).toBeGreaterThan(before)
    expect(screen.queryByText('Oslo')).not.toBeInTheDocument()
  })

  it('shows the server\'s reason and leaves the box as it was if saving fails', async () => {
    api.setBlockedColumns.mockRejectedValue(new Error('The column \'customers.city\' does not exist in this data source.'))
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(box('Block customers.city'))
    expect(await screen.findByRole('alert')).toHaveTextContent('does not exist')
    expect(box('Block customers.city')).not.toBeChecked()
  })
})

describe('views', () => {
  const withView = () => {
    const base = schema()
    return {
      ...base,
      tables: [...base.tables, {
        name: 'completed_orders', original_name: 'completed_orders', row_count: 0, primary_key: [], primary_key_inferred: false,
        is_junction: false, is_view: true, columns: [col('order_id', 'integer'), col('quantity', 'integer')],
      }],
    }
  }

  it('lists a view as a view, without a row count of zero', async () => {
    api.getSchema.mockResolvedValue(withView())
    open()
    await screen.findByRole('heading', { name: 'customers' })
    const entry = screen.getByRole('button', { name: /^completed_orders/ })
    expect(within(entry).getByText('view')).toBeInTheDocument()
    expect(within(entry).queryByText('0')).not.toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: /^orders/ })).getByText('60')).toBeInTheDocument()   // tables still show counts
  })

  it('describes the view and does not claim it has no rows', async () => {
    api.getSchema.mockResolvedValue(withView())
    api.previewTable.mockResolvedValue({ table: 'completed_orders', row_count: 0, columns: [{ name: 'order_id' }, { name: 'quantity' }],
      rows: [{ order_id: 1, quantity: 2 }, { order_id: 3, quantity: 4 }] })
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(screen.getByRole('button', { name: /^completed_orders/ }))
    expect(await screen.findByRole('heading', { name: 'completed_orders' })).toBeInTheDocument()
    expect(screen.getByText(/View of other tables\. Its rows are not counted/)).toBeInTheDocument()
    expect(screen.queryByText(/0 rows/)).not.toBeInTheDocument()
    expect(await screen.findByText('First 2 rows')).toBeInTheDocument()
    expect(screen.queryByText(/of 0 rows/)).not.toBeInTheDocument()
  })

  it('does not call a view a link table or show keys for it', async () => {
    api.getSchema.mockResolvedValue(withView())
    open()
    await screen.findByRole('heading', { name: 'customers' })
    await userEvent.click(screen.getByRole('button', { name: /^completed_orders/ }))
    await screen.findByRole('heading', { name: 'completed_orders' })
    expect(screen.queryByText(/key:/)).not.toBeInTheDocument()
  })
})
