import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '../test/fixtures.js'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))

import { AuthProvider } from '../AuthContext.jsx'
import Login from './Login.jsx'

const alice = { id: 'u1', username: 'alice' }
const open = () => render(<AuthProvider><Login /></AuthProvider>)
const ready = async () => screen.findByRole('form', { name: /Log in|Create an account/ })
const type = async (label, text) => userEvent.type(screen.getByLabelText(label), text)

beforeEach(() => {
  Object.assign(api, mockApi())
  api.authConfig.mockResolvedValue({ mode: 'accounts', registration_open: true })
})

describe('logging in', () => {
  it('has labelled fields that password managers recognise', async () => {
    open()
    await ready()
    expect(screen.getByLabelText('Username')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('sends the username without stray spaces and the password exactly as typed', async () => {
    api.login.mockResolvedValue({ token: 't', user: alice })
    open()
    await ready()
    await type('Username', '  alice ')
    await type('Password', ' a pass phrase ')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(api.login).toHaveBeenCalledWith({ username: 'alice', password: ' a pass phrase ' })
  })

  it('shows the server\'s reason when the login fails, keeps the username, and lets you try again', async () => {
    api.login.mockRejectedValue(new Error('Incorrect username or password.'))
    open()
    await ready()
    await type('Username', 'alice')
    await type('Password', 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
    expect(screen.getByLabelText('Username')).toHaveValue('alice')
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled()
  })

  it('shows a lock-out message from the server as it is', async () => {
    api.login.mockRejectedValue(new Error('Too many failed attempts. Try again in 15 minutes.'))
    open()
    await ready()
    await type('Username', 'alice')
    await type('Password', 'whatever-it-is')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 15 minutes')
  })

  it('cannot be submitted empty', async () => {
    open()
    await ready()
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(api.login).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Username')).toBeInvalid()
  })

  it('disables the button while waiting, so it cannot be sent twice', async () => {
    let finish
    api.login.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    open()
    await ready()
    await type('Username', 'alice')
    await type('Password', 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(screen.getByRole('button', { name: 'Please wait…' })).toBeDisabled()
    finish({ token: 't', user: alice })
  })
})

describe('creating an account', () => {
  const switchToRegister = async () => {
    await ready()
    await userEvent.click(screen.getByRole('button', { name: 'Create an account' }))
  }

  it('switches to a form with a repeated password and the rules stated up front', async () => {
    open()
    await switchToRegister()
    expect(screen.getByRole('heading', { name: 'Create an account' })).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByLabelText('Repeat password')).toBeInTheDocument()
    expect(screen.getByText(/At least 10 characters/)).toBeInTheDocument()
  })

  it('refuses mismatched passwords without asking the server', async () => {
    open()
    await switchToRegister()
    await type('Username', 'alice')
    await type('Password', 'a-long-password')
    await type('Repeat password', 'a-long-passwore')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match')
    expect(api.register).not.toHaveBeenCalled()
  })

  it('creates the account when the passwords match', async () => {
    api.register.mockResolvedValue({ token: 't', user: alice })
    open()
    await switchToRegister()
    await type('Username', 'alice')
    await type('Password', 'a-long-password')
    await type('Repeat password', 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(api.register).toHaveBeenCalledWith({ username: 'alice', password: 'a-long-password' })
  })

  it('shows the server\'s rule when it is broken, such as a taken name', async () => {
    api.register.mockRejectedValue(new Error('That username is already taken.'))
    open()
    await switchToRegister()
    await type('Username', 'alice')
    await type('Password', 'a-long-password')
    await type('Repeat password', 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('already taken')
  })

  it('clears the passwords, and any error, when switching back', async () => {
    api.login.mockRejectedValue(new Error('Incorrect username or password.'))
    open()
    await switchToRegister()
    await type('Username', 'alice')
    await type('Password', 'a-long-password')
    await type('Repeat password', 'something-else')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toHaveValue('alice')
  })

  it('offers no sign-up when registration is closed, and says who to ask', async () => {
    api.authConfig.mockResolvedValue({ mode: 'accounts', registration_open: false })
    open()
    await ready()
    expect(screen.queryByRole('button', { name: 'Create an account' })).not.toBeInTheDocument()
    expect(screen.getByText(/Ask your administrator/)).toBeInTheDocument()
  })
})
