import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mockApi } from '../test/fixtures.js'

const { api } = vi.hoisted(() => ({ api: {} }))
vi.mock('../services/api.js', () => ({ api }))

import { AuthProvider } from '../AuthContext.jsx'
import Account from './Account.jsx'

const open = async () => {
  render(<AuthProvider><Account /></AuthProvider>)
  return screen.findByRole('form', { name: 'Change password' })
}
const type = async (label, text) => userEvent.type(screen.getByLabelText(label), text)
const submit = () => userEvent.click(screen.getByRole('button', { name: 'Change password' }))

beforeEach(() => {
  Object.assign(api, mockApi())
  api.authConfig.mockResolvedValue({ mode: 'accounts', registration_open: true })
  api.me.mockResolvedValue({ id: 'u1', username: 'alice' })
  localStorage.setItem('tsql.token', 'tok-1')
})

describe('account page', () => {
  it('says who is logged in', async () => {
    await open()
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('changes the password and says that other devices were logged out', async () => {
    await open()
    await type('Current password', 'a-long-password')
    await type('New password', 'a-brand-new-password')
    await type('Repeat new password', 'a-brand-new-password')
    await submit()
    expect(api.changePassword).toHaveBeenCalledWith({ current_password: 'a-long-password', new_password: 'a-brand-new-password' })
    expect(await screen.findByText(/Password changed. Your other devices were logged out/)).toBeInTheDocument()
    expect(screen.getByLabelText('Current password')).toHaveValue('')               // nothing is left on screen
  })

  it('refuses mismatched new passwords without asking the server', async () => {
    await open()
    await type('Current password', 'a-long-password')
    await type('New password', 'a-brand-new-password')
    await type('Repeat new password', 'a-brand-new-passwore')
    await submit()
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match')
    expect(api.changePassword).not.toHaveBeenCalled()
  })

  it('shows the server\'s reason, such as a wrong current password, and keeps what was typed', async () => {
    api.changePassword.mockRejectedValue(new Error('The current password is incorrect.'))
    await open()
    await type('Current password', 'not-it-at-all')
    await type('New password', 'a-brand-new-password')
    await type('Repeat new password', 'a-brand-new-password')
    await submit()
    expect(await screen.findByRole('alert')).toHaveTextContent('current password is incorrect')
    expect(screen.queryByText(/Password changed/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('New password')).toHaveValue('a-brand-new-password')
  })

  it('hides the success message once something is edited again', async () => {
    await open()
    await type('Current password', 'a-long-password')
    await type('New password', 'a-brand-new-password')
    await type('Repeat new password', 'a-brand-new-password')
    await submit()
    await screen.findByText(/Password changed/)
    await type('Current password', 'x')
    expect(screen.queryByText(/Password changed/)).not.toBeInTheDocument()
  })

  it('has nothing to manage when the server has no accounts', async () => {
    api.authConfig.mockResolvedValue({ mode: 'none', registration_open: false })
    render(<AuthProvider><Account /></AuthProvider>)
    expect(await screen.findByText(/no accounts/)).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Change password' })).not.toBeInTheDocument()
  })
})
