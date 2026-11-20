import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi, sampleSource } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api, ApiError } = vi.hoisted(() => ({
  api: {},
  ApiError: class ApiError extends Error { constructor(m, status) { super(m); this.status = status } },
}))
vi.mock('../services/api.js', () => ({ api, ApiError }))

import DataSources from './DataSources.jsx'

const navigate = vi.fn()
const open = () => renderInApp(<DataSources navigate={navigate} />)
const card = async (name) => (await screen.findByRole('heading', { name })).closest('li')

beforeEach(() => {
  Object.assign(api, mockApi())
  navigate.mockClear()
})

describe('data sources page', () => {
  it('lists every source with its tables and totals', async () => {
    open()
    const sample = await card('Sample business database')
    expect(within(sample).getByText('Sample · read-only')).toBeInTheDocument()
    expect(within(sample).getByText(/2 tables · 440 rows/)).toBeInTheDocument()
    const upload = await card('Shop data')
    expect(within(upload).getByText('Uploaded')).toBeInTheDocument()
    expect(within(upload).getByText('sales')).toBeInTheDocument()
  })

  it('marks the selected source and switches to Ask or Schema for the one you pick', async () => {
    open()
    const upload = await card('Shop data')
    await userEvent.click(within(upload).getByRole('button', { name: 'Ask questions' }))
    expect(navigate).toHaveBeenCalledWith('ask')
    expect(localStorage.getItem('tsql.datasource')).toBe('up1')

    const sample = await card('Sample business database')
    await userEvent.click(within(sample).getByRole('button', { name: 'View schema' }))
    expect(navigate).toHaveBeenLastCalledWith('schema')
    expect(localStorage.getItem('tsql.datasource')).toBe('sample')
  })

  it('only lets uploaded sources be deleted, and asks first', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    open()
    const sample = await card('Sample business database')
    expect(within(sample).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()

    const upload = await card('Shop data')
    confirm.mockReturnValueOnce(false)
    await userEvent.click(within(upload).getByRole('button', { name: 'Delete' }))
    expect(api.deleteDatasource).not.toHaveBeenCalled()

    confirm.mockReturnValueOnce(true)
    await userEvent.click(within(upload).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(api.deleteDatasource).toHaveBeenCalledWith('up1'))
    await waitFor(() => expect(api.listDatasources).toHaveBeenCalledTimes(2))        // list refreshed
  })

  it('shows why a delete failed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.deleteDatasource.mockRejectedValue(new Error('Data source not found.'))
    open()
    await userEvent.click(within(await card('Shop data')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Data source not found.')
  })

  it('offers both file upload and a database connection', async () => {
    open()
    expect(await screen.findByText('Drop a CSV or Excel file here')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Connect a database' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()          // until the details are filled in
  })

  describe('an external PostgreSQL database', () => {
    const external = {
      id: 'pg1', name: 'Warehouse', kind: 'postgres', dialect: 'postgres', read_only: true,
      created_at: '2026-11-19T12:00:00+00:00',
      connection: { host: 'db.corp', port: 5432, database: 'sales', username: 'analyst', schema_name: 'public', sslmode: 'require' },
      tables: [{ name: 'orders', original_name: 'orders', row_count: 120000, columns: [] }],
    }
    beforeEach(() => api.listDatasources.mockResolvedValue([sampleSource, external]))

    it('shows where it connects to, never a password, and offers refresh and disconnect', async () => {
      open()
      const warehouse = await card('Warehouse')
      expect(within(warehouse).getByText('PostgreSQL · read-only')).toBeInTheDocument()
      expect(within(warehouse).getByText('analyst@db.corp:5432/sales · schema public')).toBeInTheDocument()
      expect(within(warehouse).getByText(/1 table · 120,000 rows/)).toBeInTheDocument()
      expect(within(warehouse).getByRole('button', { name: 'Refresh schema' })).toBeInTheDocument()
      expect(within(warehouse).getByRole('button', { name: 'Disconnect' })).toBeInTheDocument()
      expect(within(warehouse).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    })

    it('refreshes the schema and reports what was found', async () => {
      api.refreshDatasource.mockResolvedValue({ ...external, tables: [...external.tables, { name: 'extra', row_count: 1, columns: [] }] })
      open()
      await userEvent.click(within(await card('Warehouse')).getByRole('button', { name: 'Refresh schema' }))
      expect(await screen.findByText('“Warehouse” now has 2 tables.')).toBeInTheDocument()
      expect(api.refreshDatasource).toHaveBeenCalledWith('pg1')
      expect(api.listDatasources).toHaveBeenCalledTimes(2)
    })

    it('says why a refresh failed', async () => {
      api.refreshDatasource.mockRejectedValue(new Error('The database could not be reached or returned an error.'))
      open()
      await userEvent.click(within(await card('Warehouse')).getByRole('button', { name: 'Refresh schema' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('could not be reached')
    })

    it('explains that disconnecting does not touch the database, and only then removes it', async () => {
      const confirm = vi.spyOn(window, 'confirm')
      open()
      const button = within(await card('Warehouse')).getByRole('button', { name: 'Disconnect' })
      confirm.mockReturnValueOnce(false)
      await userEvent.click(button)
      expect(api.deleteDatasource).not.toHaveBeenCalled()
      confirm.mockReturnValueOnce(true)
      await userEvent.click(button)
      expect(confirm).toHaveBeenLastCalledWith(expect.stringMatching(/The database itself is not touched/))
      await waitFor(() => expect(api.deleteDatasource).toHaveBeenCalledWith('pg1'))
    })
  })

  it('reports when the server cannot be reached', async () => {
    api.listDatasources.mockRejectedValue(new ApiError('Could not reach the server. Is the backend running?', 0))
    open()
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('Could not reach the server')
  })
})
