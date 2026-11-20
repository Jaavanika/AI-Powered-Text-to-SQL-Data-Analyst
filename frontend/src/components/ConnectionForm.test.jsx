import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api, ApiError } = vi.hoisted(() => ({
  api: {},
  ApiError: class ApiError extends Error { constructor(m, status) { super(m); this.status = status } },
}))
vi.mock('../services/api.js', () => ({ api, ApiError }))

import ConnectionForm from './ConnectionForm.jsx'

const report = {
  server_version: '16.4', database: 'sales', schema_name: 'public', username: 'analyst', warnings: [],
  tables: [{ name: 'orders', original_name: 'orders', row_count: 1200, columns: [] },
    { name: 'customers', original_name: 'customers', row_count: 40, columns: [] }],
}
const connected = {
  datasource: { id: 'pg1', name: 'sales on db.corp', kind: 'postgres', tables: report.tables, connection: {} },
  warnings: [],
}

const open = (props = {}) => renderInApp(<ConnectionForm {...props} />)
const type = async (label, value) => userEvent.type(screen.getByLabelText(label), value)
const fill = async () => {
  await type('Host', 'db.corp')
  await type('Database', 'sales')
  await type('Username', 'analyst')
  await type('Password', 's3cret!')
}

beforeEach(() => {
  Object.assign(api, mockApi())
  api.testConnection.mockResolvedValue(report)
  api.connectDatabase.mockResolvedValue(connected)
})

describe('filling in the form', () => {
  it('cannot be submitted until the essentials are filled in and the port is valid', async () => {
    open()
    const test = screen.getByRole('button', { name: 'Test connection' })
    const connect = screen.getByRole('button', { name: 'Connect' })
    expect(test).toBeDisabled() && expect(connect).toBeDisabled()
    await type('Host', 'db.corp')
    await type('Database', 'sales')
    expect(connect).toBeDisabled()                                       // still no username
    await type('Username', 'analyst')
    expect(connect).toBeEnabled()
    await userEvent.clear(screen.getByLabelText('Port'))
    await userEvent.type(screen.getByLabelText('Port'), '70000')
    expect(connect).toBeDisabled()                                       // out of range
    await userEvent.clear(screen.getByLabelText('Port'))
    await userEvent.type(screen.getByLabelText('Port'), '5433')
    expect(connect).toBeEnabled()
  })

  it('hides the password and tells the user to use a read-only account', () => {
    open()
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByText(/dedicated/)).toHaveTextContent('read-only')
    expect(screen.getByText(/stored encrypted on the server and is never shown again/)).toBeInTheDocument()
  })

  it('has sensible defaults for port, schema and SSL mode', () => {
    open()
    expect(screen.getByLabelText('Port')).toHaveValue('5432')
    expect(screen.getByLabelText('Schema')).toHaveValue('public')
    expect(screen.getByLabelText('SSL mode')).toHaveValue('prefer')
  })
})

describe('testing the connection', () => {
  it('sends the details, then shows the server version, the tables and no password', async () => {
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/Connection works/)).toBeInTheDocument()
    expect(screen.getByText(/PostgreSQL 16\.4, signed in as analyst/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Tables found' })).toHaveTextContent('orders 1,200')
    expect(api.testConnection).toHaveBeenCalledWith({
      name: null, host: 'db.corp', port: 5432, database: 'sales', username: 'analyst', password: 's3cret!',
      schema_name: 'public', sslmode: 'prefer',
    })
    expect(api.connectDatabase).not.toHaveBeenCalled()
    expect(document.body).not.toHaveTextContent('s3cret')
  })

  it('shows warnings from the server', async () => {
    api.testConnection.mockResolvedValue({ ...report, warnings: ['This account can modify data in 5 table(s) (orders, customers).'] })
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByRole('list', { name: 'Warnings about this connection' })).toHaveTextContent('can modify data')
  })

  it('explains a failure and keeps what was typed', async () => {
    api.testConnection.mockRejectedValue(new ApiError("Authentication failed for user 'analyst'. Check the username and password.", 400))
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Authentication failed')
    expect(screen.getByLabelText('Host')).toHaveValue('db.corp')
    expect(screen.getByLabelText('Password')).toHaveValue('s3cret!')
    expect(screen.queryByText(/Connection works/)).not.toBeInTheDocument()
  })

  it('forgets a successful test as soon as a field changes', async () => {
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await screen.findByText(/Connection works/)
    await type('Database', 'x')
    expect(screen.queryByText(/Connection works/)).not.toBeInTheDocument()
  })

  it('shows a busy state', async () => {
    let resolve
    api.testConnection.mockReturnValue(new Promise((r) => { resolve = r }))
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(screen.getByRole('button', { name: 'Testing…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()
    resolve(report)
    await screen.findByText(/Connection works/)
  })
})

describe('connecting', () => {
  it('connects with the advanced settings, selects the new source and clears the password', async () => {
    const onConnected = vi.fn()
    api.listDatasources.mockResolvedValueOnce([]).mockResolvedValue([connected.datasource])
    open({ onConnected })
    await fill()
    await userEvent.click(screen.getByText('Schema, encryption and name'))
    await userEvent.clear(screen.getByLabelText('Schema'))
    await userEvent.type(screen.getByLabelText('Schema'), 'shop')
    await userEvent.selectOptions(screen.getByLabelText('SSL mode'), 'require')
    await userEvent.type(screen.getByLabelText('Name (optional)'), 'Warehouse')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))

    expect(await screen.findByText(/Connected to “sales on db\.corp”/)).toBeInTheDocument()
    expect(api.connectDatabase).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Warehouse', schema_name: 'shop', sslmode: 'require', password: 's3cret!' }))
    expect(onConnected).toHaveBeenCalledWith(connected.datasource)
    expect(api.listDatasources).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem('tsql.datasource')).toBe('pg1')
    expect(document.body).not.toHaveTextContent('s3cret')

    await userEvent.click(screen.getByRole('button', { name: 'Connect another database' }))
    expect(screen.getByLabelText('Password')).toHaveValue('')               // the secret did not survive
    expect(screen.getByLabelText('Host')).toHaveValue('')
  })

  it('never writes the password to browser storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await screen.findByText(/Connected to/)
    expect(setItem.mock.calls.flat().join(' ')).not.toContain('s3cret')
  })

  it('shows warnings that came with the connection', async () => {
    api.connectDatabase.mockResolvedValue({ ...connected, warnings: ['Traffic to this server may not be encrypted.'] })
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('list', { name: 'Warnings about this connection' })).toHaveTextContent('may not be encrypted')
  })

  it('shows why a connection was refused, such as a host that is not allowed', async () => {
    api.connectDatabase.mockRejectedValue(new ApiError("The host 'db.corp' is not on this server's list of allowed database hosts. Ask your administrator to add it to ALLOWED_DB_HOSTS.", 403))
    open()
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ALLOWED_DB_HOSTS')
    expect(screen.getByRole('button', { name: 'Connect' })).toBeEnabled()
  })

  it('submits with Enter', async () => {
    open()
    await fill()
    await userEvent.type(screen.getByLabelText('Username'), '{Enter}')
    await waitFor(() => expect(api.connectDatabase).toHaveBeenCalledOnce())
  })
})
