import { useEffect, useState } from 'react'
import { AppProvider, useApp } from './AppContext.jsx'
import { AuthProvider, useAuth } from './AuthContext.jsx'
import DataSourceSelector from './components/DataSourceSelector.jsx'
import ThemeToggle from './components/ThemeToggle.jsx'
import Account from './pages/Account.jsx'
import AnalyticsWorkspace from './pages/AnalyticsWorkspace.jsx'
import DataSources from './pages/DataSources.jsx'
import Definitions from './pages/Definitions.jsx'
import History from './pages/History.jsx'
import Login from './pages/Login.jsx'
import SchemaExplorer from './pages/SchemaExplorer.jsx'

const PAGES = {
  ask: AnalyticsWorkspace, history: History, context: Definitions, data: DataSources, schema: SchemaExplorer,
  account: Account,
}
const NAV = [['ask', 'Ask'], ['history', 'History'], ['context', 'Definitions'], ['data', 'Data'], ['schema', 'Schema']]

function pageFromHash() {
  const name = window.location.hash.replace(/^#\/?/, '')
  return name in PAGES ? name : 'ask'
}

function usePage() {
  const [page, setPage] = useState(pageFromHash)
  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return [page, (next) => { window.location.hash = `#/${next}` }]
}

function Shell() {
  const [page, navigate] = usePage()
  const { error } = useApp()
  const { status, user } = useAuth()
  const Page = PAGES[page]

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="topbar__brand">
          <span className="logo" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="24" height="24"><rect width="32" height="32" rx="7" fill="var(--accent)" />
              <path d="M8 21v-6M14 21V9M20 21v-9M26 21v-4" stroke="#fff" strokeWidth="3" strokeLinecap="round" /></svg>
          </span>
          <span className="topbar__name">Data Analyst</span>
        </div>
        <nav className="topbar__nav" aria-label="Main">
          {NAV.map(([key, label]) => (
            <a key={key} href={`#/${key}`} aria-current={page === key ? 'page' : undefined}>{label}</a>
          ))}
        </nav>
        <div className="topbar__tools">
          <DataSourceSelector />
          <ThemeToggle />
          {status === 'authenticated' && (
            <a className="btn btn--ghost btn--small topbar__user" href="#/account" aria-label={`Account: ${user.username}`}
              aria-current={page === 'account' ? 'page' : undefined}>
              <span aria-hidden="true">●</span> {user.username}
            </a>
          )}
        </div>
      </header>
      {error && <div className="notice notice--error banner" role="alert">{error}</div>}
      <main id="main" className="main">
        <Page navigate={navigate} />
      </main>
    </div>
  )
}

/** Shows the app once the person is known: straight away if the server has no logins, after logging in otherwise. */
function Gate() {
  const { status, user, error, retry } = useAuth()

  if (status === 'loading') return <p className="gate muted" role="status">Loading…</p>
  if (status === 'error') {
    return (
      <div className="gate">
        <div className="notice notice--error" role="alert">{error}</div>
        <button type="button" className="btn" onClick={retry}>Try again</button>
      </div>
    )
  }
  if (status === 'anonymous') return <Login />
  // Keyed by user, so nothing one person loaded can still be on screen after another logs in.
  return (
    <AppProvider key={user?.id ?? 'open'}>
      <Shell />
    </AppProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
