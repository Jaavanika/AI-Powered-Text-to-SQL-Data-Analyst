import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from './test/fixtures.js'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('./services/api.js', () => ({ api }))
vi.mock('./components/ChartViewer.jsx', () => ({ default: () => null }))

import App from './App.jsx'

const go = (hash) => { window.location.hash = hash }

beforeEach(() => {
  Object.assign(api, mockApi())
  api.getSchema.mockResolvedValue({ datasource_id: 'sample', dialect: 'sqlite', tables: [], relationships: [], many_to_many: [] })
  delete document.documentElement.dataset.theme
  go('')
})

describe('navigation', () => {
  it('opens on the Ask page and highlights the current page', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Ask a question about/ })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Ask' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Data' })).not.toHaveAttribute('aria-current')
  })

  it('follows the address bar so back and forward work', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: /Ask a question about/ })
    await userEvent.click(screen.getByRole('link', { name: 'Data' }))
    expect(await screen.findByRole('heading', { name: 'Data sources' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/data')

    await userEvent.click(screen.getByRole('link', { name: 'Schema' }))
    expect(await screen.findByRole('heading', { name: /Schema ·/ })).toBeInTheDocument()

    window.history.back()
    expect(await screen.findByRole('heading', { name: 'Data sources' })).toBeInTheDocument()
  })

  it('falls back to Ask for an unknown address', async () => {
    go('#/nowhere')
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Ask a question about/ })).toBeInTheDocument()
  })

  it('has a skip link to the main content', async () => {
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  })
})

describe('data source selector', () => {
  it('lists the data sources and remembers the choice', async () => {
    render(<App />)
    const select = await screen.findByRole('combobox', { name: 'Data' })
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2))
    expect(select).toHaveValue('sample')

    await userEvent.selectOptions(select, 'up1')
    expect(select).toHaveValue('up1')
    expect(localStorage.getItem('tsql.datasource')).toBe('up1')
    expect(await screen.findByRole('heading', { name: /Ask a question about Shop data/ })).toBeInTheDocument()
  })

  it('falls back to the first source when the saved one no longer exists', async () => {
    localStorage.setItem('tsql.datasource', 'deleted-long-ago')
    render(<App />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Data' })).toHaveValue('sample'))
  })

  it('shows a banner when the server cannot be reached', async () => {
    api.listDatasources.mockRejectedValue(new Error('Could not reach the server. Is the backend running?'))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server')
  })
})

describe('colour theme', () => {
  it('cycles auto, light and dark, applies it to the page and remembers it', async () => {
    render(<App />)
    const toggle = await screen.findByRole('button', { name: /Colour theme: auto/ })
    expect(document.documentElement.dataset.theme).toBeUndefined()

    await userEvent.click(toggle)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('tsql.theme')).toBe('light')

    await userEvent.click(screen.getByRole('button', { name: /Colour theme: light/ }))
    expect(document.documentElement.dataset.theme).toBe('dark')

    await userEvent.click(screen.getByRole('button', { name: /Colour theme: dark/ }))
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(localStorage.getItem('tsql.theme')).toBeNull()
  })

  it('starts from a saved theme', async () => {
    localStorage.setItem('tsql.theme', 'dark')
    render(<App />)
    await screen.findByRole('button', { name: /Colour theme: dark/ })
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
