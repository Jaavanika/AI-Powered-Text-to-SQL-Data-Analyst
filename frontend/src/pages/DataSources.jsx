import { useState } from 'react'
import { useApp } from '../AppContext.jsx'
import { api } from '../services/api.js'
import ConnectionForm from '../components/ConnectionForm.jsx'
import FileUpload from '../components/FileUpload.jsx'
import { formatDate } from '../lib/format.js'

const KIND_LABEL = { sample: 'Sample · read-only', upload: 'Uploaded', postgres: 'PostgreSQL · read-only' }

function DataSourceCard({ datasource, active, refreshing, onAsk, onSchema, onRefresh, onDelete }) {
  const rows = datasource.tables.reduce((sum, t) => sum + t.row_count, 0)
  const external = datasource.kind === 'postgres'
  const c = datasource.connection
  return (
    <li className={active ? 'card card--active' : 'card'}>
      <div className="card__head">
        <h3>{datasource.name}</h3>
        <span className={datasource.kind === 'upload' ? 'chip' : 'chip chip--accent'}>{KIND_LABEL[datasource.kind]}</span>
      </div>
      <p className="muted">
        {datasource.tables.length} table{datasource.tables.length === 1 ? '' : 's'} · {rows.toLocaleString('en-US')} rows
        {datasource.kind === 'upload' && ` · added ${formatDate(datasource.created_at)}`}
      </p>
      {external && c && (
        <p className="muted card__connection">
          {c.username}@{c.host}:{c.port}/{c.database} · schema {c.schema_name}
        </p>
      )}
      <ul className="chips" aria-label="Tables">
        {datasource.tables.map((t) => <li key={t.name} className="chip">{t.name}</li>)}
      </ul>
      <div className="card__actions">
        <button type="button" className="btn btn--primary btn--small" onClick={() => onAsk(datasource)}>Ask questions</button>
        <button type="button" className="btn btn--small" onClick={() => onSchema(datasource)}>View schema</button>
        {external && (
          <button type="button" className="btn btn--small" disabled={refreshing} onClick={() => onRefresh(datasource)}>
            {refreshing ? 'Refreshing…' : 'Refresh schema'}
          </button>
        )}
        {datasource.kind !== 'sample' && (
          <button type="button" className="btn btn--ghost btn--small btn--danger" onClick={() => onDelete(datasource)}>
            {external ? 'Disconnect' : 'Delete'}
          </button>
        )}
      </div>
    </li>
  )
}

export default function DataSources({ navigate }) {
  const { datasources, selectedId, setSelectedId, refresh, loading, error } = useApp()
  const [actionError, setActionError] = useState(null)
  const [refreshing, setRefreshing] = useState(null)
  const [notice, setNotice] = useState(null)

  const go = (datasource, page) => { setSelectedId(datasource.id); navigate(page) }

  const remove = async (datasource) => {
    const external = datasource.kind === 'postgres'
    const message = external
      ? `Disconnect “${datasource.name}”? Its saved credentials and your conversations about it are removed. The database itself is not touched.`
      : `Delete “${datasource.name}” and its conversations? This cannot be undone.`
    if (!window.confirm(message)) return
    setActionError(null)
    try {
      await api.deleteDatasource(datasource.id)
      await refresh()
    } catch (e) {
      setActionError(e.message)
    }
  }

  const refreshSchema = async (datasource) => {
    setRefreshing(datasource.id)
    setActionError(null)
    setNotice(null)
    try {
      const updated = await api.refreshDatasource(datasource.id)
      await refresh()
      setNotice(`“${updated.name}” now has ${updated.tables.length} table${updated.tables.length === 1 ? '' : 's'}.`)
    } catch (e) {
      setActionError(e.message)
    } finally {
      setRefreshing(null)
    }
  }

  return (
    <div className="page">
      <header className="page__head">
        <h1>Data sources</h1>
        <p className="muted">Everything you ask is answered from one data source. Uploaded files are stored privately in your workspace; connected databases are only ever read.</p>
      </header>

      {error && <div className="notice notice--error" role="alert">{error}</div>}
      {actionError && <div className="notice notice--error" role="alert">{actionError}</div>}
      {notice && <div className="notice notice--good" role="status">{notice}</div>}

      <ul className="cards" aria-label="Your data sources">
        {loading && <li className="muted">Loading…</li>}
        {datasources.map((d) => (
          <DataSourceCard key={d.id} datasource={d} active={d.id === selectedId} refreshing={refreshing === d.id}
            onAsk={(ds) => go(ds, 'ask')} onSchema={(ds) => go(ds, 'schema')} onRefresh={refreshSchema} onDelete={remove} />
        ))}
      </ul>

      <div className="two-col">
        <section className="panel" aria-labelledby="upload-title">
          <h2 id="upload-title">Upload a file</h2>
          <FileUpload onImported={() => {}} />
        </section>
        <section className="panel" aria-labelledby="connect-title">
          <h2 id="connect-title">Connect a database</h2>
          <ConnectionForm />
        </section>
      </div>
    </div>
  )
}
