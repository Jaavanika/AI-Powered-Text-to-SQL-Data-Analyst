import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './services/api.js'
import { loadSetting, saveSetting } from './lib/workspace.js'

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [datasources, setDatasources] = useState([])
  const [selectedId, setSelectedIdState] = useState(() => loadSetting('datasource'))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [health, setHealth] = useState(null)
  // Something another page wants the Ask page to do on arrival: { datasourceId, open: conversationId } or
  // { datasourceId, ask: question }. The Ask page takes it and clears it.
  const [handoff, setHandoff] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const list = await api.listDatasources()
      setDatasources(list)
      setError(null)
      return list
    } catch (e) {
      setError(e.message)
      return []
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [refresh])

  // Keep the selection valid: fall back to the first data source if the saved one is gone.
  useEffect(() => {
    if (!datasources.length) return
    if (!datasources.some((d) => d.id === selectedId)) {
      setSelectedIdState(datasources[0].id)
      saveSetting('datasource', datasources[0].id)
    }
  }, [datasources, selectedId])

  const setSelectedId = useCallback((id) => {
    setSelectedIdState(id)
    saveSetting('datasource', id)
  }, [])

  const value = useMemo(() => {
    const selected = datasources.find((d) => d.id === selectedId) || null
    return {
      datasources, selected, selectedId: selected?.id ?? null, setSelectedId, refresh, loading, error, health,
      handoff, setHandoff,
    }
  }, [datasources, selectedId, setSelectedId, refresh, loading, error, health, handoff])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>')
  return ctx
}
