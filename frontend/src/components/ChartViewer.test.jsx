import { cloneElement } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// jsdom has no layout, so give every chart a fixed size instead of measuring its container.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, ResponsiveContainer: ({ children }) => cloneElement(children, { width: 800, height: 400 }) }
})

import ChartViewer, { dedupeEntries } from './ChartViewer.jsx'

const base = { title: 't', notes: [], alternatives: [] }

describe('tooltip entries', () => {
  it('lists each series once, even though the area wash under a line repeats it', () => {
    const payload = [
      { dataKey: 'orders', name: 'Order count', value: 23 },     // the area wash
      { dataKey: 'orders', name: 'Order count', value: 23 },     // the line itself
      { dataKey: 'refunds', name: 'Refunds', value: 2 },
    ]
    expect(dedupeEntries(payload).map((p) => p.name)).toEqual(['Order count', 'Refunds'])
    expect(dedupeEntries([])).toEqual([])
  })
})

describe('ChartViewer', () => {
  it('draws nothing for tables, empty data or no spec', () => {
    expect(render(<ChartViewer spec={null} />).container).toBeEmptyDOMElement()
    expect(render(<ChartViewer spec={{ ...base, type: 'table' }} />).container).toBeEmptyDOMElement()
    expect(render(<ChartViewer spec={{ ...base, type: 'bar', x: 'a', y: ['b'], data: [] }} />).container).toBeEmptyDOMElement()
  })

  describe('bar', () => {
    const spec = {
      ...base, type: 'bar', x: 'category', y: ['revenue'], x_kind: 'category', horizontal: false,
      data: [{ category: 'A', revenue: 10 }, { category: 'B', revenue: 20 }, { category: 'C', revenue: 5 }],
    }

    it('draws one bar per category with no legend for a single series', () => {
      const { container } = render(<ChartViewer spec={spec} labels={{ revenue: 'Revenue' }} />)
      expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(3)
      expect(screen.queryByLabelText('Legend')).not.toBeInTheDocument()
      expect(screen.getByText('Revenue by category')).toBeInTheDocument()      // caption names the one series
    })

    it('labels bar tips for a single series', () => {
      const { container } = render(<ChartViewer spec={spec} />)
      const labels = [...container.querySelectorAll('.recharts-label')].map((n) => n.textContent)
      expect(labels).toEqual(['10', '20', '5'])
    })

    it('adds a legend (and drops tip labels) for several series, one colour slot each', () => {
      const multi = { ...spec, y: ['a', 'b'], data: [{ category: 'X', a: 1, b: 2 }, { category: 'Y', a: 3, b: 4 }] }
      const { container } = render(<ChartViewer spec={multi} labels={{ a: 'Alpha', b: 'Beta' }} />)
      const legend = screen.getByRole('list', { name: 'Legend' })
      expect(legend).toHaveTextContent('Alpha')
      expect(legend).toHaveTextContent('Beta')
      const swatches = [...legend.querySelectorAll('.swatch')].map((s) => s.style.background)
      expect(swatches).toEqual(['var(--series-1)', 'var(--series-2)'])
      expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(4)
      expect(container.querySelectorAll('.recharts-label')).toHaveLength(0)
    })

    it('skips tip labels when there are many bars', () => {
      const many = { ...spec, data: Array.from({ length: 15 }, (_, i) => ({ category: `c${i}`, revenue: i + 1 })) }
      const { container } = render(<ChartViewer spec={many} />)
      expect(container.querySelectorAll('.recharts-label')).toHaveLength(0)
    })

    it('can be drawn horizontally', () => {
      const { container } = render(<ChartViewer spec={{ ...spec, horizontal: true }} />)
      expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(3)
    })
  })

  it('draws a line chart and keeps the closing value as the one direct label', () => {
    const spec = {
      ...base, type: 'line', x: 'month', y: ['sales'], x_kind: 'time',
      data: [{ month: '2024-01', sales: 10 }, { month: '2024-02', sales: 30 }, { month: '2024-03', sales: 25 }],
    }
    const { container } = render(<ChartViewer spec={spec} />)
    expect(container.querySelector('.recharts-line-curve')).toBeInTheDocument()
    expect(screen.queryByLabelText('Legend')).not.toBeInTheDocument()
    const texts = [...container.querySelectorAll('svg text')].map((t) => t.textContent)
    expect(texts).toContain('25')                       // value at the end of the line
    expect(texts).not.toContain('30')                   // never a number on every point
  })

  it('gives a multi-series line a legend', () => {
    const spec = {
      ...base, type: 'line', x: 'month', y: ['Electronics', 'Furniture'], x_kind: 'time',
      data: [{ month: '2024-01', Electronics: 1, Furniture: 2 }, { month: '2024-02', Electronics: 3, Furniture: 1 },
        { month: '2024-03', Electronics: 2, Furniture: 4 }],
    }
    const { container } = render(<ChartViewer spec={spec} />)
    expect(screen.getByRole('list', { name: 'Legend' })).toHaveTextContent('Electronics')
    expect(container.querySelectorAll('.recharts-line-curve')).toHaveLength(2)
  })

  it('draws a donut with a table legend showing values and shares', () => {
    const spec = { ...base, type: 'pie', x: 'region', y: ['sales'], x_kind: 'category',
      data: [{ region: 'North', sales: 50 }, { region: 'South', sales: 30 }, { region: 'East', sales: 20 }] }
    const { container } = render(<ChartViewer spec={spec} />)
    expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(3)
    const legend = screen.getByRole('table', { name: 'Sales by region' })
    expect(legend).toHaveTextContent('North')
    expect(legend).toHaveTextContent('50%')
    expect(legend).toHaveTextContent('20%')
  })

  it('leaves zero and missing values out of a pie', () => {
    const spec = { ...base, type: 'pie', x: 'r', y: ['v'], x_kind: 'category',
      data: [{ r: 'A', v: 5 }, { r: 'B', v: 0 }, { r: 'C', v: null }] }
    const { container } = render(<ChartViewer spec={spec} />)
    expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(1)
  })

  it('draws a scatter plot', () => {
    const data = Array.from({ length: 6 }, (_, i) => ({ price: i, units: i * 2, product: `p${i}` }))
    const { container } = render(<ChartViewer spec={{ ...base, type: 'scatter', x: 'price', y: ['units'], x_kind: 'number', data }} />)
    expect(container.querySelectorAll('.recharts-scatter-symbol, .recharts-symbols, circle[r="5"]').length).toBeGreaterThanOrEqual(6)
  })

  describe('key figures', () => {
    it('shows a single figure as a hero number with its caption', () => {
      const spec = { ...base, type: 'kpi', caption: 'Laptop Pro 14', data: [{ label: 'total_revenue', value: 49362 }] }
      const { container } = render(<ChartViewer spec={spec} />)
      expect(screen.getByText('49,362')).toBeInTheDocument()
      expect(screen.getByText('Total revenue')).toBeInTheDocument()
      expect(screen.getByText('Laptop Pro 14')).toHaveClass('kpi__caption')
      expect(container.querySelector('.kpis--hero')).toBeInTheDocument()
    })

    it('shows several figures as tiles, compacting big values', () => {
      const spec = { ...base, type: 'kpi', data: [{ label: 'revenue', value: 2_500_000 }, { label: 'orders', value: 42 }] }
      const { container } = render(<ChartViewer spec={spec} />)
      expect(screen.getByText('2.5M')).toBeInTheDocument()
      expect(screen.getByText('42')).toBeInTheDocument()
      expect(container.querySelector('.kpis--hero')).not.toBeInTheDocument()
    })
  })

  it('describes the chart for assistive technology and points to the table', () => {
    const spec = { ...base, type: 'bar', x: 'category', y: ['revenue'], x_kind: 'category',
      data: [{ category: 'A', revenue: 1 }, { category: 'B', revenue: 2 }] }
    render(<ChartViewer spec={spec} />)
    expect(screen.getByRole('figure')).toHaveAccessibleName(/Bar chart of Revenue by category, 2 points. Use the table view/)
  })

  it('shows notes from the API, and dims while a new chart loads', () => {
    const spec = { ...base, type: 'bar', x: 'c', y: ['v'], x_kind: 'category', data: [{ c: 'a', v: 1 }],
      notes: ['Showing the first 25 of 60 rows; the table has them all.'] }
    render(<ChartViewer spec={spec} dimmed />)
    expect(screen.getByText(/first 25 of 60 rows/)).toBeInTheDocument()
    expect(screen.getByRole('figure')).toHaveClass('is-dimmed')
  })
})
