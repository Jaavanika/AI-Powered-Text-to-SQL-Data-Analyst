import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ResultTable from './ResultTable.jsx'

const columns = ['product_name', 'revenue', 'note']
const rows = [['Laptop', 1234.5, null], ['Mouse', 20, 'ok'], ['Desk', 999999, 'big']]

describe('ResultTable', () => {
  it('shows humanised headers and formatted cells', () => {
    render(<ResultTable columns={columns} rows={rows} />)
    expect(screen.getByRole('columnheader', { name: 'Product name' })).toBeInTheDocument()
    expect(screen.getByText('1,234.5')).toBeInTheDocument()
    expect(screen.getByText('999,999')).toBeInTheDocument()
    expect(screen.getByText('3 rows')).toBeInTheDocument()
  })

  it('right-aligns numeric columns only, and marks missing values', () => {
    render(<ResultTable columns={columns} rows={rows} />)
    expect(screen.getByRole('columnheader', { name: 'Revenue' })).toHaveClass('num')
    expect(screen.getByRole('columnheader', { name: 'Product name' })).not.toHaveClass('num')
    const missing = screen.getByText('—')
    expect(missing).toHaveClass('null')
  })

  it('says when the result was cut off', () => {
    render(<ResultTable columns={['a']} rows={[[1]]} truncated maxRows={1000} />)
    expect(screen.getByText(/cut off at the 1,000 limit, more rows exist/)).toBeInTheDocument()
  })

  it('shows 100 rows at a time', async () => {
    const many = Array.from({ length: 250 }, (_, i) => [i])
    render(<ResultTable columns={['n']} rows={many} />)
    expect(screen.getAllByRole('row')).toHaveLength(101)           // header + 100
    await userEvent.click(screen.getByRole('button', { name: /Show 100 more of 150 remaining/ }))
    expect(screen.getAllByRole('row')).toHaveLength(201)
    await userEvent.click(screen.getByRole('button', { name: /Show 50 more of 50 remaining/ }))
    expect(screen.getAllByRole('row')).toHaveLength(251)
    expect(screen.queryByRole('button', { name: /more of/ })).not.toBeInTheDocument()
  })

  it('downloads the full result as CSV, not just the visible page', async () => {
    const blobs = []
    URL.createObjectURL = vi.fn((blob) => { blobs.push(blob); return 'blob:x' })
    URL.revokeObjectURL = vi.fn()
    const clicks = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const many = Array.from({ length: 150 }, (_, i) => [i, `row, ${i}`])
    render(<ResultTable columns={['n', 'label']} rows={many} />)
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }))

    expect(clicks).toHaveBeenCalledOnce()
    const text = await blobs[0].text()
    expect(text.split('\r\n')).toHaveLength(151)
    expect(text).toContain('0,"row, 0"')
    expect(text.startsWith('n,label')).toBe(true)
  })

  it('is keyboard reachable as a labelled scroll region', () => {
    render(<ResultTable columns={['a']} rows={[[1]]} />)
    const region = screen.getByRole('region', { name: 'Query result table' })
    expect(region).toHaveAttribute('tabindex', '0')
    expect(within(region).getByRole('table')).toBeInTheDocument()
  })
})
