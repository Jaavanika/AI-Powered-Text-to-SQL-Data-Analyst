import { useState } from 'react'
import { useAuth } from '../AuthContext.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Login() {
  const { config, notice, login, register } = useAuth()
  const canRegister = Boolean(config?.registration_open)
  const [mode, setMode] = useState('login')              // 'login' | 'register'
  const [form, setForm] = useState({ username: '', password: '', repeat: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const registering = mode === 'register' && canRegister
  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }))

  const switchTo = (next) => { setMode(next); setError(null); setForm((f) => ({ ...f, password: '', repeat: '' })) }

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    if (registering && form.password !== form.repeat) return setError('The two passwords do not match.')
    setBusy(true)
    try {
      await (registering ? register : login)(form.username.trim(), form.password)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login__tools"><ThemeToggle /></div>
      <main id="main" className="login__card" aria-labelledby="login-title">
        <span className="logo" aria-hidden="true">
          <svg viewBox="0 0 32 32" width="32" height="32"><rect width="32" height="32" rx="7" fill="var(--accent)" />
            <path d="M8 21v-6M14 21V9M20 21v-9M26 21v-4" stroke="#fff" strokeWidth="3" strokeLinecap="round" /></svg>
        </span>
        <h1 id="login-title">{registering ? 'Create an account' : 'Log in'}</h1>
        <p className="muted">{registering ? 'Your data and questions stay private to your account.' : 'Log in to ask questions about your data.'}</p>

        {notice && <div className="notice notice--warn" role="status">{notice}</div>}

        <form className="login__form" onSubmit={submit} aria-label={registering ? 'Create an account' : 'Log in'}>
          <div className="field">
            <label htmlFor="login-username" className="label">Username</label>
            <input id="login-username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
              value={form.username} onChange={set('username')} maxLength={40} required autoFocus />
          </div>
          <div className="field">
            <label htmlFor="login-password" className="label">Password</label>
            <input id="login-password" type="password" autoComplete={registering ? 'new-password' : 'current-password'}
              value={form.password} onChange={set('password')} maxLength={128} required />
            {registering && <span className="muted">At least 10 characters, and not containing your username.</span>}
          </div>
          {registering && (
            <div className="field">
              <label htmlFor="login-repeat" className="label">Repeat password</label>
              <input id="login-repeat" type="password" autoComplete="new-password" value={form.repeat}
                onChange={set('repeat')} maxLength={128} required />
            </div>
          )}
          {error && <p className="inline-error" role="alert">{error}</p>}
          <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
            {busy ? 'Please wait…' : registering ? 'Create account' : 'Log in'}
          </button>
        </form>

        {canRegister ? (
          <p className="login__switch muted">
            {registering ? 'Already have an account? ' : 'New here? '}
            <button type="button" className="link-btn" onClick={() => switchTo(registering ? 'login' : 'register')}>
              {registering ? 'Log in' : 'Create an account'}
            </button>
          </p>
        ) : (
          <p className="login__switch muted">New accounts are not being accepted. Ask your administrator for one.</p>
        )}
      </main>
    </div>
  )
}
