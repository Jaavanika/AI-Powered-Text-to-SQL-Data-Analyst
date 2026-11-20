import { useState } from 'react'
import { api } from '../services/api.js'
import { useApp } from '../AppContext.jsx'

const SSL_MODES = [
  ['prefer', 'prefer (use SSL if the server offers it)'],
  ['require', 'require (always encrypt)'],
  ['verify-full', 'verify-full (encrypt and check the certificate)'],
  ['verify-ca', 'verify-ca (encrypt and check the authority)'],
  ['allow', 'allow'],
  ['disable', 'disable (no encryption)'],
]

const EMPTY = {
  name: '', host: '', port: '5432', database: '', username: '', password: '', schema_name: 'public', sslmode: 'prefer',
}

// The password lives only in this component's state while the form is open. It is sent over the
// connection to our own backend, never written to browser storage, and cleared once connected.
export default function ConnectionForm({ onConnected }) {
  const { refresh, setSelectedId } = useApp()
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(null)               // 'test' | 'connect' | null
  const [error, setError] = useState(null)
  const [report, setReport] = useState(null)           // result of a successful test
  const [connected, setConnected] = useState(null)     // { datasource, warnings }

  const set = (field) => (e) => {
    setForm((f) => ({ ...f, [field]: e.target.value }))
    setReport(null)                                    // a changed field makes an earlier test stale
  }

  const portNumber = Number(form.port)
  const valid = form.host.trim() && form.database.trim() && form.username.trim()
    && Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535 && form.schema_name.trim()

  const payload = () => ({
    name: form.name.trim() || null, host: form.host.trim(), port: portNumber, database: form.database.trim(),
    username: form.username.trim(), password: form.password, schema_name: form.schema_name.trim(), sslmode: form.sslmode,
  })

  const run = async (kind) => {
    setBusy(kind)
    setError(null)
    try {
      if (kind === 'test') {
        setReport(await api.testConnection(payload()))
      } else {
        const result = await api.connectDatabase(payload())
        await refresh()
        setSelectedId(result.datasource.id)
        setConnected(result)
        setForm(EMPTY)                                 // including the password
        setReport(null)
        onConnected?.(result.datasource)
      }
    } catch (e) {
      setError(e.message)
      setReport(null)
    } finally {
      setBusy(null)
    }
  }

  if (connected) {
    const { datasource, warnings } = connected
    return (
      <div className="notice notice--good" role="status">
        <p><strong>Connected to “{datasource.name}”.</strong> {datasource.tables.length} table{datasource.tables.length === 1 ? '' : 's'} found.</p>
        {warnings.length > 0 && (
          <ul className="plain-list" aria-label="Warnings about this connection">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
        <p className="muted">Check the Schema page for relationships between the tables, then ask your questions.</p>
        <button type="button" className="btn btn--small" onClick={() => setConnected(null)}>Connect another database</button>
      </div>
    )
  }

  return (
    <form className="connection-form" onSubmit={(e) => { e.preventDefault(); if (valid && !busy) run('connect') }}>
      <p className="muted">
        Use a dedicated <strong>read-only</strong> account. The assistant only runs read-only queries, but a read-only account
        is the safest protection. The password is stored encrypted on the server and is never shown again.
      </p>

      <div className="form-grid">
        <label className="field field--wide">
          <span className="label">Host</span>
          <input type="text" value={form.host} onChange={set('host')} placeholder="db.example.com" autoComplete="off"
            spellCheck={false} required />
        </label>
        <label className="field">
          <span className="label">Port</span>
          <input type="text" inputMode="numeric" value={form.port} onChange={set('port')} />
        </label>
        <label className="field">
          <span className="label">Database</span>
          <input type="text" value={form.database} onChange={set('database')} autoComplete="off" spellCheck={false} required />
        </label>
        <label className="field">
          <span className="label">Username</span>
          <input type="text" value={form.username} onChange={set('username')} autoComplete="off" spellCheck={false} required />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
        </label>
      </div>

      <details className="advanced">
        <summary>Schema, encryption and name</summary>
        <div className="form-grid">
          <label className="field">
            <span className="label">Schema</span>
            <input type="text" value={form.schema_name} onChange={set('schema_name')} spellCheck={false} />
          </label>
          <label className="field field--wide">
            <span className="label">SSL mode</span>
            <select value={form.sslmode} onChange={set('sslmode')}>
              {SSL_MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field field--full">
            <span className="label">Name (optional)</span>
            <input type="text" value={form.name} maxLength={100} onChange={set('name')} placeholder="Shown in the data source list" />
          </label>
        </div>
      </details>

      {error && <p className="inline-error" role="alert">{error}</p>}

      {report && (
        <div className="notice notice--good" role="status">
          <p>
            <strong>Connection works.</strong> PostgreSQL {report.server_version}, signed in as {report.username}.
            Found {report.tables.length} table{report.tables.length === 1 ? '' : 's'} in “{report.schema_name}”.
          </p>
          {report.tables.length > 0 && (
            <ul className="chips" aria-label="Tables found">
              {report.tables.slice(0, 12).map((t) => (
                <li key={t.name} className="chip">{t.name} <span className="chip__type">{t.row_count.toLocaleString('en-US')}</span></li>
              ))}
              {report.tables.length > 12 && <li className="chip">+{report.tables.length - 12} more</li>}
            </ul>
          )}
          {report.warnings.length > 0 && (
            <ul className="plain-list" aria-label="Warnings about this connection">
              {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="form-actions">
        <button type="button" className="btn" disabled={!valid || !!busy} onClick={() => run('test')}>
          {busy === 'test' ? 'Testing…' : 'Test connection'}
        </button>
        <button type="submit" className="btn btn--primary" disabled={!valid || !!busy}>
          {busy === 'connect' ? 'Connecting…' : 'Connect'}
        </button>
      </div>
    </form>
  )
}
