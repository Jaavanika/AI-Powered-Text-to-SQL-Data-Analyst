import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './services/api.js'
import { clearToken, getToken, setToken } from './lib/session.js'

const AuthContext = createContext(null)

/**
 * Who is using the app.
 *   loading        asking the server how it is set up
 *   open           the server has no logins (AUTH_MODE=none): nothing to do
 *   anonymous      logins are on and nobody is logged in
 *   authenticated  a user is logged in
 *   error          the server could not be reached
 */
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', config: null, user: null, notice: null, error: null })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: 'loading', error: null }))
    try {
      const config = await api.authConfig()
      if (config.mode !== 'accounts') return setState({ status: 'open', config, user: null, notice: null, error: null })
      if (!getToken()) return setState({ status: 'anonymous', config, user: null, notice: null, error: null })
      try {
        const user = await api.me()
        setState({ status: 'authenticated', config, user, notice: null, error: null })
      } catch (e) {
        if (e.status !== 401) throw e
        clearToken()                                   // an old or ended session: just ask to log in
        setState({ status: 'anonymous', config, user: null, notice: null, error: null })
      }
    } catch (e) {
      setState((s) => ({ ...s, status: 'error', error: e.message }))
    }
  }, [])

  useEffect(() => { load() }, [load])

  // The server says the session is no longer valid (it expired, or was ended from another device).
  useEffect(() => api.onUnauthorized((message) => {
    clearToken()
    setState((s) => (s.status === 'authenticated'
      ? { ...s, status: 'anonymous', user: null, notice: message || 'Your session has ended. Log in again.' }
      : s))
  }), [])

  const signIn = useCallback(async (kind, username, password) => {
    const session = await api[kind]({ username, password })
    setToken(session.token)
    setState((s) => ({ ...s, status: 'authenticated', user: session.user, notice: null }))
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* the token is dropped here either way */
    }
    clearToken()
    setState((s) => ({ ...s, status: 'anonymous', user: null, notice: null }))
  }, [])

  const value = useMemo(() => ({
    ...state,
    login: (username, password) => signIn('login', username, password),
    register: (username, password) => signIn('register', username, password),
    logout,
    retry: load,
  }), [state, signIn, logout, load])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
