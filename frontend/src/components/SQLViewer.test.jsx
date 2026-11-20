import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import SQLViewer, { highlight } from './SQLViewer.jsx'

const SQL = "SELECT name, COUNT(*) AS n\nFROM customers\nWHERE city = 'Oslo' AND age > 30\nGROUP BY name"

describe('SQLViewer', () => {
  it('renders nothing without SQL', () => {
    expect(render(<SQLViewer sql={null} />).container).toBeEmptyDOMElement()
  })

  it('is collapsed by default but still shows the trust signals', () => {
    render(<SQLViewer sql={SQL} validation={{ valid: true }} tablesUsed={['customers']} />)
    expect(screen.getByRole('button', { name: /How this was answered/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText(/Validated · read-only/)).toBeInTheDocument()
    expect(screen.getByText('customers')).toBeInTheDocument()
    expect(screen.queryByLabelText('Generated SQL')).not.toBeInTheDocument()
  })

  it('expands to show the explanation, assumptions and SQL', async () => {
    render(<SQLViewer sql={SQL} explanation="Counts customers per name." assumptions={['Ignores nulls.']} />)
    await userEvent.click(screen.getByRole('button', { name: /How this was answered/ }))
    expect(screen.getByText('Counts customers per name.')).toBeInTheDocument()
    expect(screen.getByText('Ignores nulls.')).toBeInTheDocument()
    expect(screen.getByLabelText('Generated SQL')).toHaveTextContent('FROM customers')
  })

  it('reports corrections and lets you see the rejected attempts', async () => {
    const attempts = [{ sql: 'SELECT nope FROM t', error: "Unknown column 'nope'." }]
    render(<SQLViewer sql={SQL} attempts={attempts} defaultOpen />)
    expect(screen.getByText('Corrected 1×')).toBeInTheDocument()
    await userEvent.click(screen.getByText('1 earlier attempt was rejected'))
    expect(screen.getByText("Unknown column 'nope'.")).toBeInTheDocument()
    expect(screen.getByText('SELECT nope FROM t')).toBeInTheDocument()
  })

  it('copies the SQL', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.assign(navigator, { clipboard: { writeText } })
    render(<SQLViewer sql={SQL} defaultOpen />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith(SQL)
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })
})

describe('highlight', () => {
  it('marks keywords, strings and numbers and keeps every character', () => {
    const { container } = render(<pre>{highlight(SQL)}</pre>)
    expect(container.textContent).toBe(SQL)
    const kinds = (cls) => [...container.querySelectorAll(`.${cls}`)].map((n) => n.textContent)
    expect(kinds('sql-kw')).toEqual(['SELECT', 'COUNT', 'AS', 'FROM', 'WHERE', 'AND', 'GROUP BY'].filter((k) => k !== 'COUNT'))
    expect(kinds('sql-str')).toEqual(["'Oslo'"])
    expect(kinds('sql-num')).toEqual(['30'])
  })

  it('never turns SQL text into markup', () => {
    const { container } = render(<pre>{highlight("SELECT '<img src=x onerror=alert(1)>' FROM t")}</pre>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})
