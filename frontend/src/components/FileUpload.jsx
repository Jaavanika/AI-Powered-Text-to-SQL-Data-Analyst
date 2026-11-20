import { useRef, useState } from 'react'
import { api } from '../services/api.js'
import { useApp } from '../AppContext.jsx'
import { formatCell, humanize } from '../lib/format.js'

const ACCEPT = '.csv,.xlsx,.xls'

function SheetReview({ sheet, state, onChange }) {
  return (
    <fieldset className="sheet">
      <legend className="sr-only">{sheet.sheet_name}</legend>
      <div className="sheet__head">
        <label className="check">
          <input type="checkbox" checked={state.selected} onChange={(e) => onChange({ selected: e.target.checked })} />
          <span>Import <strong>{sheet.sheet_name}</strong></span>
        </label>
        <span className="muted">{sheet.row_count.toLocaleString('en-US')} rows · {sheet.columns.length} columns</span>
      </div>
      {state.selected && (
        <>
          <label className="field">
            <span className="label">Table name</span>
            <input type="text" value={state.tableName} onChange={(e) => onChange({ tableName: e.target.value })} />
          </label>
          <ul className="chips" aria-label={`Columns in ${sheet.sheet_name}`}>
            {sheet.columns.map((c) => (
              <li key={c.name} className="chip" title={`Original header: ${c.original_name}`}>
                {c.name} <span className="chip__type">{c.type}</span>
              </li>
            ))}
          </ul>
          <details className="preview">
            <summary>Preview the first {sheet.preview_rows.length} rows</summary>
            <div className="result-table__scroll" tabIndex={0}>
              <table>
                <thead><tr>{sheet.columns.map((c) => <th key={c.name} scope="col">{humanize(c.name)}</th>)}</tr></thead>
                <tbody>
                  {sheet.preview_rows.map((row, i) => (
                    <tr key={i}>{sheet.columns.map((c) => <td key={c.name} className={row[c.name] == null ? 'null' : undefined}>{formatCell(row[c.name])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </fieldset>
  )
}

export default function FileUpload({ onImported }) {
  const { datasources, refresh, setSelectedId } = useApp()
  const inputRef = useRef(null)
  const [step, setStep] = useState('idle')          // idle | previewing | review | importing | done
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState(null)
  const [preview, setPreview] = useState(null)
  const [sheets, setSheets] = useState({})          // sheet_name -> { selected, tableName }
  const [target, setTarget] = useState('new')       // 'new' | datasource id
  const [name, setName] = useState('')
  const [replace, setReplace] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [imported, setImported] = useState(null)

  const uploads = datasources.filter((d) => d.kind === 'upload')

  const reset = () => {
    setStep('idle'); setError(null); setPreview(null); setSheets({}); setTarget('new'); setName('')
    setReplace(false); setConflict(false); setImported(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const choose = async (file) => {
    if (!file) return
    setError(null)
    setStep('previewing')
    try {
      const result = await api.previewUpload(file)
      setPreview(result)
      setSheets(Object.fromEntries(result.sheets.map((s) => [s.sheet_name, { selected: true, tableName: s.suggested_table_name }])))
      setName(result.filename.replace(/\.[^.]+$/, ''))
      setStep('review')
    } catch (e) {
      setError(e.message)
      setStep('idle')
    }
  }

  const chosenSheets = preview?.sheets.filter((s) => sheets[s.sheet_name]?.selected) ?? []

  const doImport = async () => {
    setError(null)
    setStep('importing')
    try {
      const datasource = await api.importUpload({
        upload_id: preview.upload_id,
        datasource_id: target === 'new' ? null : target,
        datasource_name: target === 'new' ? name.trim() || null : null,
        tables: chosenSheets.map((s) => ({ sheet_name: s.sheet_name, table_name: sheets[s.sheet_name].tableName || null })),
        if_exists: replace ? 'replace' : 'fail',
      })
      await refresh()
      setSelectedId(datasource.id)
      setImported(datasource)
      setStep('done')
      onImported?.(datasource)
    } catch (e) {
      setConflict(e.status === 409)
      setError(e.message)
      setStep('review')
    }
  }

  if (step === 'done' && imported) {
    return (
      <div className="notice notice--good" role="status">
        <p><strong>Imported into “{imported.name}”.</strong> It now has {imported.tables.length} table{imported.tables.length === 1 ? '' : 's'}: {imported.tables.map((t) => t.name).join(', ')}.</p>
        <p className="muted">If you uploaded several related files, confirm how they connect on the Schema page before asking questions across them.</p>
        <button type="button" className="btn btn--small" onClick={reset}>Upload another file</button>
      </div>
    )
  }

  if (step === 'review' || step === 'importing') {
    return (
      <div className="upload-review">
        <div className="upload-review__head">
          <h3>{preview.filename}</h3>
          <button type="button" className="btn btn--ghost btn--small" onClick={reset} disabled={step === 'importing'}>Choose another file</button>
        </div>
        {preview.warnings.length > 0 && (
          <ul className="notice notice--warn plain-list" aria-label="Warnings about this file">
            {preview.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
        {preview.sheets.map((sheet) => (
          <SheetReview key={sheet.sheet_name} sheet={sheet} state={sheets[sheet.sheet_name]}
            onChange={(change) => setSheets((s) => ({ ...s, [sheet.sheet_name]: { ...s[sheet.sheet_name], ...change } }))} />
        ))}
        <p className="muted">Table and column names are cleaned to lowercase letters, digits and underscores. Dates are detected and stored as ISO text.</p>

        <fieldset className="target">
          <legend className="label">Where should it go?</legend>
          <label className="check">
            <input type="radio" name="target" checked={target === 'new'} onChange={() => setTarget('new')} />
            <span>A new data source</span>
          </label>
          {target === 'new' && (
            <label className="field field--indent">
              <span className="label">Name</span>
              <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
          {uploads.length > 0 && (
            <>
              <label className="check">
                <input type="radio" name="target" checked={target !== 'new'} onChange={() => setTarget(uploads[0].id)} />
                <span>Add to an existing upload (so the tables can be joined)</span>
              </label>
              {target !== 'new' && (
                <label className="field field--indent">
                  <span className="label">Data source</span>
                  <select value={target} onChange={(e) => setTarget(e.target.value)}>
                    {uploads.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                </label>
              )}
            </>
          )}
        </fieldset>

        {conflict && (
          <label className="check">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            <span>Replace the existing tables that have the same name</span>
          </label>
        )}
        {error && <p className="inline-error" role="alert">{error}</p>}
        <button type="button" className="btn btn--primary" onClick={doImport}
          disabled={step === 'importing' || chosenSheets.length === 0 || (conflict && !replace)}>
          {step === 'importing' ? 'Importing…' : `Import ${chosenSheets.length} table${chosenSheets.length === 1 ? '' : 's'}`}
        </button>
      </div>
    )
  }

  return (
    <div>
      <div
        className={dragging ? 'dropzone is-dragging' : 'dropzone'}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files?.[0]) }}
      >
        <p className="dropzone__title">{step === 'previewing' ? 'Reading the file…' : 'Drop a CSV or Excel file here'}</p>
        <p className="muted">.csv, .xlsx or .xls. Excel workbooks can have several sheets.</p>
        <label className="btn btn--primary">
          Choose a file
          <input ref={inputRef} type="file" accept={ACCEPT} className="sr-only" disabled={step === 'previewing'}
            onChange={(e) => choose(e.target.files?.[0])} />
        </label>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </div>
  )
}
