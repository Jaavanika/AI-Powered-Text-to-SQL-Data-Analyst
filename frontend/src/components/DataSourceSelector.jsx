import { useApp } from '../AppContext.jsx'

export default function DataSourceSelector() {
  const { datasources, selectedId, setSelectedId, loading } = useApp()
  return (
    <label className="selector">
      <span className="selector__label">Data</span>
      <select value={selectedId ?? ''} disabled={loading || datasources.length === 0} onChange={(e) => setSelectedId(e.target.value)}>
        {datasources.length === 0 && <option value="">{loading ? 'Loading…' : 'No data sources'}</option>}
        {datasources.map((d) => (
          <option key={d.id} value={d.id}>{d.name}</option>
        ))}
      </select>
    </label>
  )
}
