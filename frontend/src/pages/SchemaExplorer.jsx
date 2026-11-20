import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApp } from '../AppContext.jsx'
import { api } from '../services/api.js'
import ResultTable from '../components/ResultTable.jsx'
import SchemaDiagram from '../components/SchemaDiagram.jsx'
import { formatPercent } from '../lib/format.js'

const CARDINALITY = { many_to_one: 'many-to-one', one_to_one: 'one-to-one' }
const columnsOf = (r, side) => (side === 'from' ? r.from_columns : r.to_columns).join(', ')

function RelationshipLine({ from, to, children }) {
  return (
    <span className="relation">
      <code>{from}</code> <span aria-label="references">→</span> <code>{to}</code>{children}
    </span>
  )
}

export default function SchemaExplorer({ navigate }) {
  const { selected } = useApp()
  const [schema, setSchema] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [table, setTable] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [showDiagram, setShowDiagram] = useState(true)
  const [blocked, setBlocked] = useState({ columns: [], locked: [] })
  const [previewTick, setPreviewTick] = useState(0)    // bumped when a saved change means the preview must be read again

  // Only the bundled sample is fixed. A connected database is read-only, but you can still confirm
  // relationships between its tables, since those are notes kept here and not changes to the database.
  const readOnly = selected?.kind === 'sample'

  const load = useCallback(async () => {
    if (!selected) return
    try {
      const [s, sug, bl] = await Promise.all([
        api.getSchema(selected.id), api.suggestRelationships(selected.id), api.getBlockedColumns(selected.id)])
      setSchema(s)
      setSuggestions(sug)
      setBlocked(bl)
      setError(null)
      setTable((t) => (s.tables.some((x) => x.name === t) ? t : s.tables[0]?.name ?? null))
    } catch (e) {
      setError(e.message)
    }
  }, [selected])

  useEffect(() => {
    setSchema(null); setSuggestions([]); setPreview(null); setTable(null); setBlocked({ columns: [], locked: [] }); load()
  }, [load])

  useEffect(() => {
    if (!selected || !table) return
    let cancelled = false
    api.previewTable(selected.id, table, 10)
      .then((p) => { if (!cancelled) setPreview(p) })
      .catch(() => { if (!cancelled) setPreview(null) })
    return () => { cancelled = true }
  }, [selected, table, previewTick])

  const current = schema?.tables.find((t) => t.name === table)

  // Foreign-key markers per column of the current table.
  const references = useMemo(() => {
    const map = {}
    for (const r of schema?.relationships ?? []) {
      if (r.from_table === table) r.from_columns.forEach((c, i) => { map[c] = `${r.to_table}.${r.to_columns[i]}` })
    }
    return map
  }, [schema, table])

  // What is blocked on the current table: the whole table, or single columns, by you or by the administrator.
  const entries = useMemo(() => new Set([...blocked.columns, ...blocked.locked]), [blocked])
  const tableBlocked = (name) => entries.has(`${name}.*`)
  const columnBlocked = (name, column) => tableBlocked(name) || entries.has(`${name}.${column}`)
  const lockedHere = (entry) => blocked.locked.includes(entry)

  const toggleBlocked = async (entry, on) => {
    const next = new Set(blocked.columns)
    if (on) next.add(entry)
    else next.delete(entry)
    const before = blocked
    setBlocked({ ...blocked, columns: [...next] })            // tick the box now; the save follows
    setBusy(`block:${entry}`)
    setError(null)
    try {
      setBlocked(await api.setBlockedColumns(selected.id, [...next]))
      setPreviewTick((t) => t + 1)                             // only now does the server agree, so only now re-read
    } catch (e) {
      setBlocked(before)
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const confirm = async (s) => {
    setBusy(`${s.from_table}.${s.from_column}`)
    setError(null)
    try {
      await api.confirmRelationship(selected.id, {
        from_table: s.from_table, from_column: s.from_column, to_table: s.to_table, to_column: s.to_column,
      })
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (r) => {
    setBusy(r.id)
    setError(null)
    try {
      await api.deleteRelationship(selected.id, r.id)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  if (!selected) {
    return (
      <div className="page page--center">
        <p className="muted">Choose a data source to see its schema.</p>
        <button type="button" className="btn btn--primary" onClick={() => navigate('data')}>Go to data sources</button>
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page__head">
        <h1>Schema · {selected.name}</h1>
        <p className="muted">The tables, columns and relationships the assistant can use. Joins only follow relationships listed here.</p>
        <p className="muted">Block anything that must never be read, such as personal data. A blocked column is hidden from the assistant, left out of previews, and refused in every query.</p>
      </header>
      {error && <div className="notice notice--error" role="alert">{error}</div>}
      {!schema && !error && <p className="muted">Loading the schema…</p>}

      {schema && (
        <>
          {!readOnly && schema.relationships.length === 0 && schema.tables.length > 1 && (
            <div className="notice notice--warn" role="status">
              These tables have no relationships yet, so questions that need data from more than one of them cannot be answered.
              {suggestions.length > 0 ? ' Confirm the suggestions below.' : ' No obvious relationships were found.'}
            </div>
          )}

          <div className="schema">
            <nav className="schema__tables" aria-label="Tables">
              <ul>
                {schema.tables.map((t) => (
                  <li key={t.name}>
                    <button type="button" className={t.name === table ? 'is-active' : undefined}
                      aria-current={t.name === table ? 'true' : undefined} onClick={() => setTable(t.name)}>
                      <span>{t.name}{t.is_junction && <span className="chip chip--small">link table</span>}</span>
                      <span className="muted">{t.is_view ? 'view' : t.row_count.toLocaleString('en-US')}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </nav>

            {current && (
              <section className="schema__detail" aria-label={`Table ${current.name}`}>
                <h2>{current.name}</h2>
                <p className="muted">
                  {current.is_view ? 'View of other tables. Its rows are not counted.' : `${current.row_count.toLocaleString('en-US')} rows`}
                  {current.primary_key.length > 0 && ` · key: ${current.primary_key.join(', ')}${current.primary_key_inferred ? ' (inferred)' : ''}`}
                </p>
                <label className="check blocked-toggle">
                  <input type="checkbox" checked={tableBlocked(current.name)}
                    disabled={lockedHere(`${current.name}.*`) || busy === `block:${current.name}.*`}
                    onChange={(e) => toggleBlocked(`${current.name}.*`, e.target.checked)} />
                  <span>Block this whole table{lockedHere(`${current.name}.*`) && ' (set by your administrator)'}</span>
                </label>
                <div className="result-table__scroll" tabIndex={0}>
                  <table>
                    <thead><tr><th scope="col">Column</th><th scope="col">Original header</th><th scope="col">Type</th><th scope="col">Key</th><th scope="col">Blocked</th></tr></thead>
                    <tbody>
                      {current.columns.map((c) => (
                        <tr key={c.name}>
                          <td><code>{c.name}</code></td>
                          <td className="muted">{c.original_name !== c.name ? c.original_name : ''}</td>
                          <td><span className="chip chip--small">{c.type}</span></td>
                          <td>
                            {c.is_primary_key && <span className="chip chip--small chip--accent">primary</span>}
                            {references[c.name] && <span className="chip chip--small">→ {references[c.name]}</span>}
                          </td>
                          <td>
                            <input type="checkbox" aria-label={`Block ${current.name}.${c.name}`}
                              checked={columnBlocked(current.name, c.name)}
                              disabled={tableBlocked(current.name) || lockedHere(`${current.name}.${c.name}`) || busy === `block:${current.name}.${c.name}`}
                              title={lockedHere(`${current.name}.${c.name}`) ? 'Set by your administrator' : undefined}
                              onChange={(e) => toggleBlocked(`${current.name}.${c.name}`, e.target.checked)} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!preview && tableBlocked(current.name) && (
                  <p className="muted">This table is blocked, so no rows are shown.</p>
                )}
                {preview && (
                  <div className="schema__preview">
                    <h3>First rows</h3>
                    <ResultTable columns={preview.columns.map((c) => c.name)} rows={preview.rows.map((r) => preview.columns.map((c) => r[c.name]))}
                      caption={current?.is_view ? `First ${preview.rows.length} rows`
                        : `First ${preview.rows.length} of ${preview.row_count.toLocaleString('en-US')} rows`} />
                  </div>
                )}
              </section>
            )}
          </div>

          {schema.tables.length > 1 && (
            <section className="panel" aria-labelledby="diagram-title">
              <div className="panel__head">
                <h2 id="diagram-title">Relationship diagram</h2>
                <button type="button" className="btn btn--small" aria-expanded={showDiagram} onClick={() => setShowDiagram((s) => !s)}>
                  {showDiagram ? 'Hide diagram' : 'Show diagram'}
                </button>
              </div>
              {showDiagram && <SchemaDiagram schema={schema} selected={table} onSelect={setTable} />}
            </section>
          )}

          <section className="panel" aria-labelledby="rel-title">
            <h2 id="rel-title">Relationships</h2>
            {schema.relationships.length === 0 ? (
              <p className="muted">None yet.</p>
            ) : (
              <ul className="rel-list">
                {schema.relationships.map((r) => (
                  <li key={r.id}>
                    <RelationshipLine from={`${r.from_table}.${columnsOf(r, 'from')}`} to={`${r.to_table}.${columnsOf(r, 'to')}`}>
                      <span className="chip chip--small">{CARDINALITY[r.cardinality]}</span>
                      <span className="chip chip--small">{r.source === 'declared' ? 'declared in database' : 'confirmed by you'}</span>
                    </RelationshipLine>
                    {r.source === 'user' && (
                      <button type="button" className="btn btn--ghost btn--small" disabled={busy === r.id} onClick={() => remove(r)}>Remove</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {schema.many_to_many.length > 0 && (
              <p className="muted">
                Many-to-many: {schema.many_to_many.map((m) => `${m.table_a} ↔ ${m.table_b} via ${m.via}`).join('; ')}.
              </p>
            )}

            {!readOnly && suggestions.length > 0 && (
              <>
                <h3>Suggested relationships</h3>
                <p className="muted">Found by comparing column names and values. They are not used until you confirm them.</p>
                <ul className="rel-list">
                  {suggestions.map((s) => {
                    const key = `${s.from_table}.${s.from_column}`
                    return (
                      <li key={`${key}->${s.to_table}.${s.to_column}`}>
                        <div>
                          <RelationshipLine from={key} to={`${s.to_table}.${s.to_column}`}>
                            <span className="chip chip--small">{CARDINALITY[s.cardinality]}</span>
                            <span className="chip chip--small">{formatPercent(s.match_ratio, 1)} of values match</span>
                          </RelationshipLine>
                          <p className="muted rel-list__reason">{s.reason}</p>
                        </div>
                        <button type="button" className="btn btn--small btn--primary" disabled={busy === key} onClick={() => confirm(s)}>
                          {busy === key ? 'Confirming…' : 'Confirm'}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </>
            )}
          </section>
        </>
      )}
    </div>
  )
}
