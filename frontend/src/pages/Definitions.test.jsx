import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { definition, mockApi, uploadSource } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))

import Definitions from './Definitions.jsx'

const open = () => renderInApp(<Definitions />)
const revenue = definition()
const best = definition({ id: 'd2', kind: 'term', name: 'Best-performing product', aliases: [], description: 'Highest revenue.', expression: null })
const note = definition({ id: 'd3', kind: 'column', name: 'orders.status', aliases: [], description: 'completed, cancelled or returned', expression: null })

const form = async () => within(await screen.findByRole('form', { name: 'Add a definition' }))
const csv = (text, name = 'glossary.csv') => new File([text], name, { type: 'text/csv' })

beforeEach(() => {
  Object.assign(api, mockApi())
  api.listContext.mockResolvedValue([revenue, best, note])
})

describe('the list', () => {
  it('groups definitions by kind, with other names, formula and description', async () => {
    open()
    const metrics = await screen.findByRole('list', { name: 'Metrics' })
    expect(within(metrics).getByText('Revenue')).toBeInTheDocument()
    expect(within(metrics).getByText('sales')).toBeInTheDocument()
    expect(within(metrics).getByText('Money from completed orders only.')).toBeInTheDocument()
    expect(within(metrics).getByText(/SUM\(CASE WHEN orders.status/)).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Terms' })).getByText('Best-performing product')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Column notes' })).getByText('orders.status')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Table notes' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your definitions (3)' })).toBeInTheDocument()
    expect(api.listContext).toHaveBeenCalledWith('sample')
  })

  it('invites you to start when there are none, with examples offered for the sample database', async () => {
    api.listContext.mockResolvedValue([])
    open()
    expect(await screen.findByText(/Nothing defined yet/)).toBeInTheDocument()
    expect(screen.getByText(/Add the examples/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add example definitions' })).toBeEnabled()
  })

  it('offers no examples for an uploaded data source', async () => {
    localStorage.setItem('tsql.datasource', uploadSource.id)
    api.listContext.mockResolvedValue([])
    open()
    expect(await screen.findByText(/import a glossary/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add example definitions' })).not.toBeInTheDocument()
    expect(api.listContext).toHaveBeenCalledWith('up1')
  })

  it('shows an error when the definitions cannot be loaded', async () => {
    api.listContext.mockRejectedValue(new Error('Could not reach the server.'))
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
  })
})

describe('adding', () => {
  it('saves a metric with its other names and formula, then lists it', async () => {
    api.createContext.mockResolvedValue(definition({ id: 'd9', name: 'Profit' }))
    open()
    const f = await form()
    await userEvent.type(f.getByLabelText('Name'), '  Profit ')
    await userEvent.type(f.getByLabelText(/Other names/), 'margin,  net income ,')
    await userEvent.type(f.getByLabelText('What it means'), 'Revenue minus cost.')
    await userEvent.type(f.getByLabelText(/SQL formula/), 'SUM(orders.quantity)')
    api.listContext.mockResolvedValue([revenue, definition({ id: 'd9', name: 'Profit' })])
    await userEvent.click(f.getByRole('button', { name: 'Add definition' }))

    expect(api.createContext).toHaveBeenCalledWith('sample', {
      kind: 'metric', name: 'Profit', aliases: ['margin', 'net income'],
      description: 'Revenue minus cost.', expression: 'SUM(orders.quantity)',
    })
    expect(await screen.findByText('Added “Profit”.')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Your definitions (2)' })).toBeInTheDocument()
    expect(f.getByLabelText('Name')).toHaveValue('')              // ready for the next one
  })

  it('shows only the fields that make sense for each kind, and sends none of the others', async () => {
    api.createContext.mockResolvedValue(definition({ id: 'd8', kind: 'column' }))
    open()
    const f = await form()
    expect(f.getByLabelText(/SQL formula/)).toBeInTheDocument()
    await userEvent.click(f.getByRole('button', { name: 'Term' }))
    expect(f.queryByLabelText(/SQL formula/)).not.toBeInTheDocument()
    expect(f.getByLabelText(/Other names/)).toBeInTheDocument()
    await userEvent.click(f.getByRole('button', { name: 'Column note' }))
    expect(f.queryByLabelText(/Other names/)).not.toBeInTheDocument()
    expect(f.getByLabelText('Column (table.column)')).toBeInTheDocument()

    await userEvent.type(f.getByLabelText('Column (table.column)'), 'orders.status')
    await userEvent.type(f.getByLabelText('What it means'), 'one of three values')
    await userEvent.click(f.getByRole('button', { name: 'Add definition' }))
    expect(api.createContext).toHaveBeenCalledWith('sample', {
      kind: 'column', name: 'orders.status', aliases: [], description: 'one of three values', expression: null,
    })
  })

  it('suggests real tables and columns of the data source', async () => {
    open()
    const f = await form()
    await userEvent.click(f.getByRole('button', { name: 'Column note' }))
    const options = [...document.querySelectorAll('datalist option')].map((o) => o.value)
    expect(options).toEqual(expect.arrayContaining(['customers.customer_id', 'orders.order_date']))
    await userEvent.click(f.getByRole('button', { name: 'Table note' }))
    expect([...document.querySelectorAll('datalist option')].map((o) => o.value)).toEqual(['customers', 'orders'])
  })

  it('shows the server’s reason when a definition is rejected, and keeps what was typed', async () => {
    api.createContext.mockRejectedValue(new Error('The formula uses a column that does not exist: orders.total.'))
    open()
    const f = await form()
    await userEvent.type(f.getByLabelText('Name'), 'Revenue2')
    await userEvent.type(f.getByLabelText('What it means'), 'text')
    await userEvent.type(f.getByLabelText(/SQL formula/), 'SUM(orders.total)')
    await userEvent.click(f.getByRole('button', { name: 'Add definition' }))
    expect(await f.findByRole('alert')).toHaveTextContent('does not exist: orders.total')
    expect(f.getByLabelText(/SQL formula/)).toHaveValue('SUM(orders.total)')
    expect(f.getByRole('button', { name: 'Add definition' })).toBeEnabled()
  })

  it('needs a name and a description before it will send anything', async () => {
    open()
    const f = await form()
    await userEvent.click(f.getByRole('button', { name: 'Add definition' }))
    expect(api.createContext).not.toHaveBeenCalled()
    expect(f.getByLabelText('Name')).toBeInvalid()
  })
})

describe('editing and deleting', () => {
  it('edits in place: the kind is fixed, the fields are filled in, and saving updates that entry', async () => {
    api.updateContext.mockResolvedValue({ ...revenue, description: 'Changed.' })
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Revenue' }))
    const f = within(await screen.findByRole('form', { name: 'Edit definition' }))
    expect(f.getByLabelText('Name')).toHaveValue('Revenue')
    expect(f.getByLabelText(/Other names/)).toHaveValue('sales')
    expect(f.getByRole('button', { name: 'Term' })).toBeDisabled()
    await userEvent.clear(f.getByLabelText('What it means'))
    await userEvent.type(f.getByLabelText('What it means'), 'Changed.')
    await userEvent.click(f.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(api.updateContext).toHaveBeenCalledWith('sample', 'd1', expect.objectContaining({
      kind: 'metric', name: 'Revenue', aliases: ['sales'], description: 'Changed.',
    })))
    expect(await screen.findByText('Updated “Revenue”.')).toBeInTheDocument()
    expect(await screen.findByRole('form', { name: 'Add a definition' })).toBeInTheDocument()   // back to adding
  })

  it('can cancel an edit', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Revenue' }))
    await userEvent.click(within(await screen.findByRole('form', { name: 'Edit definition' })).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('form', { name: 'Add a definition' })).toBeInTheDocument()
    expect(api.updateContext).not.toHaveBeenCalled()
  })

  it('deletes after confirmation, and only then', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    open()
    const remove = await screen.findByRole('button', { name: 'Delete Revenue' })
    confirm.mockReturnValueOnce(false)
    await userEvent.click(remove)
    expect(api.deleteContext).not.toHaveBeenCalled()
    confirm.mockReturnValueOnce(true)
    api.listContext.mockResolvedValue([best, note])
    await userEvent.click(remove)
    await waitFor(() => expect(api.deleteContext).toHaveBeenCalledWith('sample', 'd1'))
    expect(await screen.findByText('Deleted “Revenue”.')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Edit Revenue' })).not.toBeInTheDocument())
  })
})

describe('examples', () => {
  it('adds the example definitions and reloads the list', async () => {
    api.listContext.mockResolvedValueOnce([])
    api.addContextExamples.mockResolvedValue({ created: 5, updated: 0, skipped: 0, errors: [] })
    open()
    api.listContext.mockResolvedValue([revenue, best, note])
    await userEvent.click(await screen.findByRole('button', { name: 'Add example definitions' }))
    expect(await screen.findByText('Added 5 example definitions.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Metrics' })).toBeInTheDocument()
    expect(api.addContextExamples).toHaveBeenCalledWith('sample')
  })

  it('says when the examples were already there', async () => {
    api.addContextExamples.mockResolvedValue({ created: 0, updated: 0, skipped: 5, errors: [] })
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Add example definitions' }))
    expect(await screen.findByText('The examples are already here.')).toBeInTheDocument()
  })
})

describe('importing a glossary', () => {
  const found = {
    entries: [
      { kind: 'term', name: 'Churn', aliases: [], description: 'Customers who stopped buying.', expression: null },
      { kind: 'metric', name: 'ARPU', aliases: [], description: 'Average revenue per user.', expression: null },
    ],
    warnings: ['Row 4 was skipped: it has no description.'],
  }
  const pick = async (file) => userEvent.upload(await screen.findByLabelText('Glossary file'), file)

  it('shows what was found, with warnings, before anything is saved', async () => {
    api.previewContextImport.mockResolvedValue(found)
    open()
    await pick(csv('name,description\nChurn,x'))
    const preview = await screen.findByLabelText('Import preview')
    expect(within(preview).getByText(/found in glossary.csv/)).toHaveTextContent('2 definitions found')
    expect(within(preview).getByText('Churn')).toBeInTheDocument()
    expect(within(preview).getByText(/Row 4 was skipped/)).toBeInTheDocument()
    expect(api.previewContextImport).toHaveBeenCalledWith('sample', expect.objectContaining({ name: 'glossary.csv' }))
    expect(api.importContext).not.toHaveBeenCalled()
  })

  it('imports on confirmation, keeping existing definitions by default, and reports the outcome', async () => {
    api.previewContextImport.mockResolvedValue(found)
    api.importContext.mockResolvedValue({ created: 1, updated: 0, skipped: 1, errors: [] })
    open()
    await pick(csv('x'))
    await userEvent.click(await screen.findByRole('button', { name: 'Import 2 definitions' }))
    expect(api.importContext).toHaveBeenCalledWith('sample', { entries: found.entries, mode: 'skip' })
    expect(await screen.findByText('Added 1, updated 0, skipped 1.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Import preview')).not.toBeInTheDocument()
    await waitFor(() => expect(api.listContext.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('can replace existing definitions instead', async () => {
    api.previewContextImport.mockResolvedValue(found)
    api.importContext.mockResolvedValue({ created: 0, updated: 2, skipped: 0, errors: [] })
    open()
    await pick(csv('x'))
    await userEvent.click(await screen.findByLabelText('Replace it with the imported one'))
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 definitions' }))
    expect(api.importContext).toHaveBeenCalledWith('sample', expect.objectContaining({ mode: 'replace' }))
  })

  it('lists the entries that could not be imported', async () => {
    api.previewContextImport.mockResolvedValue(found)
    api.importContext.mockResolvedValue({ created: 1, updated: 0, skipped: 0, errors: ['ARPU: the formula uses an unknown column.'] })
    open()
    await pick(csv('x'))
    await userEvent.click(await screen.findByRole('button', { name: 'Import 2 definitions' }))
    expect(await screen.findByText('ARPU: the formula uses an unknown column.')).toBeInTheDocument()
  })

  it('cancels without saving', async () => {
    api.previewContextImport.mockResolvedValue(found)
    open()
    await pick(csv('x'))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText('Import preview')).not.toBeInTheDocument()
    expect(api.importContext).not.toHaveBeenCalled()
  })

  it('shows why a file could not be read, and offers nothing to import', async () => {
    api.previewContextImport.mockRejectedValue(new Error('This file is too large. Keep it under 200 KB.'))
    open()
    await pick(csv('x'))
    expect(await screen.findByText(/too large/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Import preview')).not.toBeInTheDocument()
  })

  it('will not import a file that held nothing usable', async () => {
    api.previewContextImport.mockResolvedValue({ entries: [], warnings: ['No definitions were found.'] })
    open()
    await pick(csv('x'))
    expect(await screen.findByRole('button', { name: 'Import 0 definitions' })).toBeDisabled()
  })
})
