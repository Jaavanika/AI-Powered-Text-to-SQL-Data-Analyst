import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi, sampleSource, uploadSource } from '../test/fixtures.js'
import { renderInApp } from '../test/render.jsx'

const { api, ApiError } = vi.hoisted(() => ({
  api: {},
  ApiError: class ApiError extends Error { constructor(m, status) { super(m); this.status = status } },
}))
vi.mock('../services/api.js', () => ({ api, ApiError }))

import FileUpload from './FileUpload.jsx'

const preview = {
  upload_id: 'u1', filename: 'business_data.xlsx', file_type: 'xlsx',
  warnings: ["Sheet 'Empty' is empty and was skipped."],
  sheets: [
    { sheet_name: 'Customers', suggested_table_name: 'customers', row_count: 12,
      columns: [{ name: 'customer_id', original_name: 'Customer ID', type: 'integer' }, { name: 'city', original_name: 'City', type: 'text' }],
      preview_rows: [{ customer_id: 1, city: 'Oslo' }, { customer_id: 2, city: null }] },
    { sheet_name: 'Orders', suggested_table_name: 'orders', row_count: 60,
      columns: [{ name: 'order_id', original_name: 'Order ID', type: 'integer' }], preview_rows: [{ order_id: 1 }] },
  ],
}
const imported = { ...uploadSource, id: 'new1', name: 'business_data', tables: [{ name: 'customers' }, { name: 'orders' }] }

const file = (name = 'business_data.xlsx') => new File(['x'], name)
const pick = async (f = file()) => userEvent.upload(document.querySelector('input[type=file]'), f)
const open = (props = {}) => renderInApp(<FileUpload {...props} />)

beforeEach(() => {
  Object.assign(api, mockApi())
  api.previewUpload.mockResolvedValue(preview)
  api.importUpload.mockResolvedValue(imported)
})

describe('choosing a file', () => {
  it('previews every sheet with its columns, types and warnings', async () => {
    open()
    await pick()
    expect(await screen.findByText('business_data.xlsx')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Warnings about this file' })).toHaveTextContent("Sheet 'Empty' is empty");
    ['Customers', 'Orders'].forEach((s) => expect(screen.getByRole('checkbox', { name: new RegExp(`Import ${s}`) })).toBeChecked())
    expect(screen.getByRole('list', { name: 'Columns in Customers' })).toHaveTextContent('customer_id integer')
    expect(screen.getByDisplayValue('customers')).toBeInTheDocument()
    expect(api.previewUpload).toHaveBeenCalledWith(expect.objectContaining({ name: 'business_data.xlsx' }))
  })

  it('shows a readable error when the file is rejected, and lets you try again', async () => {
    api.previewUpload.mockRejectedValueOnce(new ApiError('Unsupported file type. Upload a .csv, .xlsx or .xls file.', 415))
    open()
    // The file picker filters by extension, so a wrong type can only arrive by drag and drop.
    fireEvent.drop(screen.getByText('Drop a CSV or Excel file here').closest('.dropzone'),
      { dataTransfer: { files: [file('notes.txt')] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('Unsupported file type')
    expect(screen.getByText('Drop a CSV or Excel file here')).toBeInTheDocument()
  })

  it('accepts a dropped file', async () => {
    open()
    const zone = screen.getByText('Drop a CSV or Excel file here').closest('.dropzone')
    const dropped = file('dropped.csv')
    fireEvent.drop(zone, { dataTransfer: { files: [dropped] } })
    await screen.findByText('business_data.xlsx')
    expect(api.previewUpload).toHaveBeenCalledWith(dropped)
  })
})

describe('importing', () => {
  it('imports the selected sheets under the chosen names into a new data source', async () => {
    const onImported = vi.fn()
    // after the import the server's list includes the new source, as in real use
    api.listDatasources.mockResolvedValueOnce([sampleSource, uploadSource]).mockResolvedValue([sampleSource, uploadSource, imported])
    open({ onImported })
    await pick()
    await screen.findByText('business_data.xlsx')

    await userEvent.click(screen.getByRole('checkbox', { name: /Import Orders/ }))      // leave Orders out
    const name = screen.getByDisplayValue('customers')
    await userEvent.clear(name)
    await userEvent.type(name, 'clients')
    await userEvent.clear(screen.getByLabelText('Name', { exact: true }))
    await userEvent.type(screen.getByLabelText('Name', { exact: true }), 'My shop')
    await userEvent.click(screen.getByRole('button', { name: 'Import 1 table' }))

    await waitFor(() => expect(api.importUpload).toHaveBeenCalledWith({
      upload_id: 'u1', datasource_id: null, datasource_name: 'My shop',
      tables: [{ sheet_name: 'Customers', table_name: 'clients' }], if_exists: 'fail',
    }))
    expect(await screen.findByText(/Imported into “business_data”/)).toBeInTheDocument()
    expect(onImported).toHaveBeenCalledWith(imported)
    expect(api.listDatasources).toHaveBeenCalledTimes(2)                // initial load + refresh after the import
    expect(localStorage.getItem('tsql.datasource')).toBe('new1')        // the new source is selected
  })

  it('can add to an existing upload so tables can be joined', async () => {
    open()
    await screen.findByText('Drop a CSV or Excel file here')
    await waitFor(() => expect(api.listDatasources).toHaveBeenCalled())
    await pick()
    await screen.findByText('business_data.xlsx')
    await userEvent.click(await screen.findByRole('radio', { name: /Add to an existing upload/ }))
    expect(screen.getByRole('combobox', { name: 'Data source' })).toHaveValue('up1')      // only uploads are offered
    expect(within(screen.getByRole('combobox', { name: 'Data source' })).queryByText('Sample business database')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 tables' }))
    await waitFor(() => expect(api.importUpload).toHaveBeenCalledWith(expect.objectContaining({
      datasource_id: 'up1', datasource_name: null })))
  })

  it('handles a table-name conflict by offering to replace', async () => {
    api.importUpload.mockRejectedValueOnce(new ApiError("Table(s) already exist: customers. Rename them or use if_exists='replace'.", 409))
    open()
    await pick()
    await screen.findByText('business_data.xlsx')
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 tables' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already exist')
    const replace = screen.getByRole('checkbox', { name: /Replace the existing tables/ })
    expect(screen.getByRole('button', { name: 'Import 2 tables' })).toBeDisabled()      // must opt in first
    await userEvent.click(replace)
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 tables' }))
    await waitFor(() => expect(api.importUpload).toHaveBeenLastCalledWith(expect.objectContaining({ if_exists: 'replace' })))
  })

  it('cannot import with nothing selected', async () => {
    open()
    await pick()
    await screen.findByText('business_data.xlsx')
    await userEvent.click(screen.getByRole('checkbox', { name: /Import Customers/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /Import Orders/ }))
    expect(screen.getByRole('button', { name: 'Import 0 tables' })).toBeDisabled()
  })

  it('lets you start over from the review or after finishing', async () => {
    open()
    await pick()
    await screen.findByText('business_data.xlsx')
    await userEvent.click(screen.getByRole('button', { name: 'Choose another file' }))
    expect(screen.getByText('Drop a CSV or Excel file here')).toBeInTheDocument()

    await pick()
    await screen.findByText('business_data.xlsx')
    await userEvent.click(screen.getByRole('button', { name: 'Import 2 tables' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Upload another file' }))
    expect(screen.getByText('Drop a CSV or Excel file here')).toBeInTheDocument()
  })
})
