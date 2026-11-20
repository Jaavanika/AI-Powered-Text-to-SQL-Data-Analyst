import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useApp } from '../AppContext.jsx'
import { api } from '../services/api.js'

const KINDS = [
  { value: 'metric', label: 'Metric', plural: 'Metrics', nameLabel: 'Name', hint: 'A number you calculate, such as revenue or profit.' },
  { value: 'term', label: 'Term', plural: 'Terms', nameLabel: 'Term', hint: 'A phrase with a business meaning, such as “active customer”.' },
  { value: 'table', label: 'Table note', plural: 'Table notes', nameLabel: 'Table', hint: 'What one row of a table is, or how to use it.' },
  { value: 'column', label: 'Column note', plural: 'Column notes', nameLabel: 'Column (table.column)', hint: 'What the values of a column mean.' },
]
const KIND = Object.fromEntries(KINDS.map((k) => [k.value, k]))
const EMPTY = { kind: 'metric', name: '', aliases: '', description: '', expression: '' }

const splitAliases = (text) => text.split(',').map((a) => a.trim()).filter(Boolean)

function toPayload(form) {
  const named = form.kind === 'metric' || form.kind === 'term'
  return {
    kind: form.kind,
    name: form.name.trim(),
    aliases: named ? splitAliases(form.aliases) : [],
    description: form.description.trim(),
    expression: form.kind === 'metric' && form.expression.trim() ? form.expression.trim() : null,
  }
}

function EntryForm({ datasource, editing, onSaved, onCancel }) {
  const [form, setForm] = useState(editing ? {
    kind: editing.kind, name: editing.name, aliases: editing.aliases.join(', '),
    description: editing.description, expression: editing.expression ?? '',
  } : EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const listId = useId()
  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }))
  const kind = KIND[form.kind]

  const suggestions = useMemo(() => {
    const tables = datasource?.tables ?? []
    if (form.kind === 'table') return tables.map((t) => t.name)
    if (form.kind === 'column') return tables.flatMap((t) => t.columns.map((c) => `${t.name}.${c.name}`))
    return []
  }, [datasource, form.kind])

  const submit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const payload = toPayload(form)
      const saved = editing
        ? await api.updateContext(datasource.id, editing.id, payload)
        : await api.createContext(datasource.id, payload)
      setForm(EMPTY)
      onSaved(saved, Boolean(editing))
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="panel definition-form" onSubmit={submit} aria-label={editing ? 'Edit definition' : 'Add a definition'}>
      <h2>{editing ? `Edit “${editing.name}”` : 'Add a definition'}</h2>

      <div className="segmented" role="group" aria-label="Kind of definition">
        {KINDS.map((k) => (
          <button key={k.value} type="button" className={form.kind === k.value ? 'is-active' : undefined}
            aria-pressed={form.kind === k.value} disabled={Boolean(editing)}
            onClick={() => setForm((f) => ({ ...f, kind: k.value }))}>{k.label}</button>
        ))}
      </div>
      <p className="muted">{kind.hint}</p>

      <div className="field">
        <label htmlFor="def-name" className="label">{kind.nameLabel}</label>
        <input id="def-name" type="text" value={form.name} onChange={set('name')} maxLength={80} required list={suggestions.length ? listId : undefined}
          placeholder={form.kind === 'column' ? 'orders.status' : form.kind === 'table' ? 'orders' : 'Revenue'} />
        {suggestions.length > 0 && <datalist id={listId}>{suggestions.map((s) => <option key={s} value={s} />)}</datalist>}
      </div>

      {(form.kind === 'metric' || form.kind === 'term') && (
        <div className="field">
          <label htmlFor="def-aliases" className="label">Other names (optional, separated by commas)</label>
          <input id="def-aliases" type="text" value={form.aliases} onChange={set('aliases')} placeholder="sales, turnover" />
        </div>
      )}

      <div className="field">
        <label htmlFor="def-description" className="label">What it means</label>
        <textarea id="def-description" rows={3} value={form.description} onChange={set('description')} maxLength={600} required
          placeholder="Money earned from completed orders: quantity times price. Cancelled and returned orders do not count." />
      </div>

      {form.kind === 'metric' && (
        <div className="field">
          <label htmlFor="def-expression" className="label">SQL formula (optional)</label>
          <input id="def-expression" type="text" className="mono" value={form.expression} onChange={set('expression')} maxLength={400}
            placeholder="SUM(orders.quantity * products.price)" />
          <span className="muted">Checked against your tables and columns when you save. The formula guides the AI; it is not run on its own.</span>
        </div>
      )}

      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="btn btn--primary" disabled={saving}>
          {saving ? 'Saving…' : editing ? 'Save changes' : 'Add definition'}
        </button>
        {editing && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}

function ImportPanel({ datasourceId, onImported }) {
  const input = useRef(null)
  const [preview, setPreview] = useState(null)         // { name, entries, warnings }
  const [mode, setMode] = useState('skip')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [outcome, setOutcome] = useState(null)

  const choose = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setBusy(true)
    setError(null)
    setOutcome(null)
    try {
      const out = await api.previewContextImport(datasourceId, file)
      setPreview({ name: file.name, ...out })
    } catch (err) {
      setPreview(null)
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      const out = await api.importContext(datasourceId, { entries: preview.entries, mode })
      setOutcome(out)
      setPreview(null)
      onImported()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel" aria-labelledby="import-title">
      <h2 id="import-title">Import a glossary</h2>
      <p className="muted">
        Upload a data dictionary or glossary as .csv, .json, .md or .txt. You see what was found before anything is saved.
      </p>
      <div className="form-actions">
        <input ref={input} type="file" accept=".csv,.json,.md,.txt" className="sr-only" aria-label="Glossary file" onChange={choose} />
        <button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>Choose a file…</button>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}

      {preview && (
        <div className="import-preview" aria-label="Import preview">
          <p><strong>{preview.entries.length}</strong> {preview.entries.length === 1 ? 'definition' : 'definitions'} found in {preview.name}.</p>
          {preview.warnings.length > 0 && (
            <div className="notice notice--warn"><ul className="plain-list">{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
          )}
          <ul className="import-preview__list">
            {preview.entries.map((entry, i) => (
              <li key={`${entry.kind}-${entry.name}-${i}`}>
                <span className="chip">{KIND[entry.kind].label}</span> <strong>{entry.name}</strong>
                <span className="muted"> — {entry.description}</span>
              </li>
            ))}
          </ul>
          <fieldset className="target">
            <legend className="label">If a definition already exists</legend>
            <label className="check"><input type="radio" name="import-mode" checked={mode === 'skip'} onChange={() => setMode('skip')} /> Keep the existing one</label>
            <label className="check"><input type="radio" name="import-mode" checked={mode === 'replace'} onChange={() => setMode('replace')} /> Replace it with the imported one</label>
          </fieldset>
          <div className="form-actions">
            <button type="button" className="btn btn--primary" disabled={busy || preview.entries.length === 0} onClick={confirm}>
              {busy ? 'Importing…' : `Import ${preview.entries.length} ${preview.entries.length === 1 ? 'definition' : 'definitions'}`}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </div>
      )}

      {outcome && (
        <div className={outcome.errors.length ? 'notice notice--warn' : 'notice notice--good'} role="status">
          <p>Added {outcome.created}, updated {outcome.updated}, skipped {outcome.skipped}.</p>
          {outcome.errors.length > 0 && <ul className="plain-list">{outcome.errors.map((m, i) => <li key={i}>{m}</li>)}</ul>}
        </div>
      )}
    </section>
  )
}

function Entry({ entry, onEdit, onDelete }) {
  return (
    <li className="definition">
      <div className="definition__head">
        <h4 className="definition__name">{entry.name}</h4>
        {entry.aliases.length > 0 && (
          <span className="chips" aria-label={`Other names for ${entry.name}`}>
            {entry.aliases.map((a) => <span key={a} className="chip">{a}</span>)}
          </span>
        )}
      </div>
      <p>{entry.description}</p>
      {entry.expression && <code className="definition__expression">{entry.expression}</code>}
      <div className="card__actions">
        <button type="button" className="btn btn--small" aria-label={`Edit ${entry.name}`} onClick={() => onEdit(entry)}>Edit</button>
        <button type="button" className="btn btn--ghost btn--small btn--danger" aria-label={`Delete ${entry.name}`}
          onClick={() => onDelete(entry)}>Delete</button>
      </div>
    </li>
  )
}

export default function Definitions() {
  const { selected, loading } = useApp()
  const datasourceId = selected?.id
  const [entries, setEntries] = useState(null)
  const [editing, setEditing] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    try {
      setEntries(await api.listContext(datasourceId))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [datasourceId])

  useEffect(() => {
    setEntries(null)
    setEditing(null)
    setNotice(null)
    if (datasourceId) reload()
  }, [datasourceId, reload])

  if (!selected) {
    return (
      <div className="page page--center">
        <p className="muted">{loading ? 'Loading your data sources…' : 'Choose a data source to define its terms.'}</p>
      </div>
    )
  }

  const saved = (entry, wasEdit) => {
    setEditing(null)
    setNotice(`${wasEdit ? 'Updated' : 'Added'} “${entry.name}”.`)
    reload()
  }

  const remove = async (entry) => {
    if (!window.confirm(`Delete the definition of “${entry.name}”?`)) return
    try {
      await api.deleteContext(datasourceId, entry.id)
      setNotice(`Deleted “${entry.name}”.`)
      if (editing?.id === entry.id) setEditing(null)
      reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const addExamples = async () => {
    setBusy(true)
    setError(null)
    try {
      const out = await api.addContextExamples(datasourceId)
      setNotice(out.created ? `Added ${out.created} example ${out.created === 1 ? 'definition' : 'definitions'}.` : 'The examples are already here.')
      reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const groups = KINDS.map((k) => ({ ...k, items: (entries ?? []).filter((e) => e.kind === k.value) })).filter((g) => g.items.length)

  return (
    <div className="page">
      <header className="page__head">
        <h1>Definitions</h1>
        <p className="muted">
          Teach the analyst how <strong>{selected.name}</strong> talks. When a question uses a term you defined, such as
          “revenue”, the answer follows your definition instead of a guess. Definitions only change what a question means;
          every query is still checked and run read-only.
        </p>
      </header>

      {error && <div className="notice notice--error" role="alert">{error}</div>}
      {notice && <div className="notice notice--good" role="status">{notice}</div>}

      <div className="definitions-layout">
        <section aria-labelledby="defined-title" className="definitions-list">
          <div className="panel__head">
            <h2 id="defined-title">Your definitions{entries && entries.length > 0 && ` (${entries.length})`}</h2>
            {selected.kind === 'sample' && (
              <button type="button" className="btn btn--small" disabled={busy} onClick={addExamples}>Add example definitions</button>
            )}
          </div>
          {!entries && !error && <p className="muted" role="status">Loading…</p>}
          {entries && entries.length === 0 && (
            <p className="muted empty">
              Nothing defined yet.{selected.kind === 'sample' ? ' Add the examples to see how it works, or write your own.' : ' Add your first definition, or import a glossary.'}
            </p>
          )}
          {groups.map((group) => (
            <div key={group.value} className="definition-group">
              <h3>{group.plural}</h3>
              <ul className="definition-list" aria-label={group.plural}>
                {group.items.map((entry) => <Entry key={entry.id} entry={entry} onEdit={setEditing} onDelete={remove} />)}
              </ul>
            </div>
          ))}
        </section>

        <div className="definitions-side">
          <EntryForm key={editing?.id ?? 'new'} datasource={selected} editing={editing} onSaved={saved} onCancel={() => setEditing(null)} />
          <ImportPanel datasourceId={datasourceId} onImported={reload} />
        </div>
      </div>
    </div>
  )
}
