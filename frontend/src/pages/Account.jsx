import { useState } from 'react'
import { useAuth } from '../AuthContext.jsx'
import { api } from '../services/api.js'

const EMPTY = { current: '', next: '', repeat: '' }

export default function Account() {
  const { status, user, logout } = useAuth()
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)
  const set = (field) => (e) => { setDone(false); setForm((f) => ({ ...f, [field]: e.target.value })) }

  if (status !== 'authenticated') {
    return (
      <div className="page page--center">
        <p className="muted">This server has no accounts, so there is nothing to manage here.</p>
      </div>
    )
  }

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setDone(false)
    if (form.next !== form.repeat) return setError('The two new passwords do not match.')
    setBusy(true)
    try {
      await api.changePassword({ current_password: form.current, new_password: form.next })
      setForm(EMPTY)
      setDone(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page__head">
        <h1>Account</h1>
        <p className="muted">Signed in as <strong>{user.username}</strong>. Your data sources, questions and definitions belong to this account only.</p>
      </header>

      <div className="two-col">
        <section className="panel" aria-labelledby="password-title">
          <h2 id="password-title">Change password</h2>
          <form className="account-form" onSubmit={submit} aria-label="Change password">
            <div className="field">
              <label htmlFor="pw-current" className="label">Current password</label>
              <input id="pw-current" type="password" autoComplete="current-password" value={form.current}
                onChange={set('current')} required />
            </div>
            <div className="field">
              <label htmlFor="pw-new" className="label">New password</label>
              <input id="pw-new" type="password" autoComplete="new-password" value={form.next} onChange={set('next')}
                maxLength={128} required />
              <span className="muted">At least 10 characters, and not containing your username.</span>
            </div>
            <div className="field">
              <label htmlFor="pw-repeat" className="label">Repeat new password</label>
              <input id="pw-repeat" type="password" autoComplete="new-password" value={form.repeat} onChange={set('repeat')}
                maxLength={128} required />
            </div>
            {error && <p className="inline-error" role="alert">{error}</p>}
            {done && <p className="notice notice--good" role="status">Password changed. Your other devices were logged out.</p>}
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
            </div>
          </form>
        </section>

        <section className="panel" aria-labelledby="session-title">
          <h2 id="session-title">This device</h2>
          <p className="muted">Logging out ends the session on this device only.</p>
          <div className="form-actions">
            <button type="button" className="btn" onClick={logout}>Log out</button>
          </div>
        </section>
      </div>
    </div>
  )
}
