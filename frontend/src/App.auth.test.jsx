import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi, sampleSource, uploadSource } from './test/fixtures.js'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('./services/api.js', () => ({ api }))
vi.mock('./components/ChartViewer.jsx', () => ({ default: () => null }))

import App from './App.jsx'

const alice = { id: 'u1', username: 'alice' }
const accountsOn = (registrationOpen = true) =>
  api.authConfig.mockResolvedValue({ mode: 'accounts', registration_open: registrationOpen })
const saveLogin = (token = 'tok-1') => localStorage.setItem('tsql.token', token)
const heading = (name) => screen.findByRole('heading', { name })
const goToAccount = async () => userEvent.click(await screen.findByRole('link', { name: 'Account: alice' }))

beforeEach(() => {
  Object.assign(api, mockApi())
  api.getSchema.mockResolvedValue({ datasource_id: 'sample', dialect: 'sqlite', tables: [], relationships: [], many_to_many: [] })
  delete document.documentElement.dataset.theme
  window.location.hash = ''
})

describe('logging in', () => {
  it('goes straight to the app when the server has no logins', async () => {
    render(<App />)
    expect(await heading(/Ask a question about/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Account/ })).not.toBeInTheDocument()
    expect(api.me).not.toHaveBeenCalled()
  })

  it('shows the login screen, and loads no data, when logins are on and nobody is logged in', async () => {
    accountsOn()
    render(<App />)
    expect(await heading('Log in')).toBeInTheDocument()
    expect(api.listDatasources).not.toHaveBeenCalled()
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })

  it('logs in, remembers the token, and shows the app with the account in the top bar', async () => {
    accountsOn()
    api.login.mockResolvedValue({ token: 'tok-1', user: alice })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Username'), 'alice')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(api.login).toHaveBeenCalledWith({ username: 'alice', password: 'a-long-password' })
    expect(await heading(/Ask a question about/)).toBeInTheDocument()
    expect(localStorage.getItem('tsql.token')).toBe('tok-1')
    expect(screen.getByRole('link', { name: 'Account: alice' })).toHaveAttribute('href', '#/account')
  })

  it('resumes a saved login without asking for the password again', async () => {
    accountsOn()
    saveLogin()
    api.me.mockResolvedValue(alice)
    render(<App />)
    expect(await heading(/Ask a question about/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Log in' })).not.toBeInTheDocument()
  })

  it('drops a saved token the server no longer accepts, without an alarming message', async () => {
    accountsOn()
    saveLogin('stale')
    api.me.mockRejectedValue(Object.assign(new Error('Your session has expired. Log in again.'), { status: 401 }))
    render(<App />)
    expect(await heading('Log in')).toBeInTheDocument()
    expect(localStorage.getItem('tsql.token')).toBeNull()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('returns to the login screen, saying why, when the session ends while using the app', async () => {
    accountsOn()
    saveLogin()
    api.me.mockResolvedValue(alice)
    render(<App />)
    await heading(/Ask a question about/)
    const [handler] = api.onUnauthorized.mock.calls.at(-1)
    act(() => handler('Your session has expired. Log in again.'))
    expect(await heading('Log in')).toBeInTheDocument()
    expect(screen.getByText('Your session has expired. Log in again.')).toBeInTheDocument()
    expect(localStorage.getItem('tsql.token')).toBeNull()
  })

  it('logs out from the account page, ends the session on the server, and shows the login screen', async () => {
    accountsOn()
    saveLogin()
    api.me.mockResolvedValue(alice)
    render(<App />)
    await goToAccount()
    await userEvent.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await heading('Log in')).toBeInTheDocument()
    expect(api.logout).toHaveBeenCalled()
    expect(localStorage.getItem('tsql.token')).toBeNull()
  })

  it('still logs out here if the server cannot be told', async () => {
    accountsOn()
    saveLogin()
    api.me.mockResolvedValue(alice)
    api.logout.mockRejectedValue(new Error('Could not reach the server.'))
    render(<App />)
    await goToAccount()
    await userEvent.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await heading('Log in')).toBeInTheDocument()
    expect(localStorage.getItem('tsql.token')).toBeNull()
  })

  it('does not show one person data after another logs in on the same page', async () => {
    accountsOn()
    saveLogin()
    api.me.mockResolvedValue(alice)
    api.listDatasources.mockResolvedValue([sampleSource, { ...uploadSource, id: 'alices', name: 'Alice private data' }])
    render(<App />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Data' })).toHaveTextContent('Alice private data'))
    await goToAccount()
    await userEvent.click(await screen.findByRole('button', { name: 'Log out' }))
    await heading('Log in')

    api.listDatasources.mockResolvedValue([sampleSource])
    api.login.mockResolvedValue({ token: 'tok-2', user: { id: 'u2', username: 'bob' } })
    await userEvent.type(screen.getByLabelText('Username'), 'bob')
    await userEvent.type(screen.getByLabelText('Password'), 'another-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    const select = await screen.findByRole('combobox', { name: 'Data' })
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(1))
    expect(screen.queryByText('Alice private data')).not.toBeInTheDocument()
  })

  it('says so, with a retry, when the server cannot be reached', async () => {
    api.authConfig.mockRejectedValueOnce(new Error('Could not reach the server. Is the backend running?'))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await heading(/Ask a question about/)).toBeInTheDocument()
  })

  it('applies the saved colour theme on the login screen too', async () => {
    accountsOn()
    localStorage.setItem('tsql.theme', 'dark')
    render(<App />)
    await heading('Log in')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
