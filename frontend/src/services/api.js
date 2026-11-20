import { getWorkspaceId } from '../lib/workspace.js'
import { getToken } from '../lib/session.js'

export class ApiError extends Error {
  constructor(message, status, details = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

// FastAPI reports errors as a string, an object ({message, errors}) or a list of validation errors.
export function messageFromDetail(detail, fallback) {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => d?.msg || (typeof d === 'string' ? d : null)).filter(Boolean)
    return parts.length ? parts.join('; ') : fallback
  }
  if (typeof detail === 'object') {
    const errors = Array.isArray(detail.errors) ? detail.errors.join(' ') : ''
    return [detail.message, errors].filter(Boolean).join(' ') || fallback
  }
  return fallback
}

// Told when the server rejects the login token (it expired, or was ended elsewhere), so the app can ask to log in again.
let unauthorizedHandler = null

// `open` requests (logging in, registering) carry no token and never count as "the session ended".
async function request(path, { method = 'GET', json, body, signal, open = false } = {}) {
  const headers = { 'X-Workspace-Id': getWorkspaceId() }
  const token = open ? null : getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (json !== undefined) headers['Content-Type'] = 'application/json'

  let response
  try {
    response = await fetch(path, {
      method,
      headers,
      body: json !== undefined ? JSON.stringify(json) : body,
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('Could not reach the server. Is the backend running?', 0)
  }

  if (response.status === 204) return null
  let data = null
  try {
    data = await response.json()
  } catch {
    /* non-JSON body */
  }
  if (!response.ok) {
    const fallback = `Request failed (${response.status}).`
    const message = messageFromDetail(data?.detail, fallback)
    if (response.status === 401 && token) unauthorizedHandler?.(message)
    throw new ApiError(message, response.status, data?.detail ?? null)
  }
  return data
}

const enc = encodeURIComponent

export const api = {
  health: () => request('/health'),

  onUnauthorized: (handler) => {
    unauthorizedHandler = handler
    return () => { if (unauthorizedHandler === handler) unauthorizedHandler = null }
  },
  authConfig: () => request('/api/auth/config', { open: true }),
  register: (credentials) => request('/api/auth/register', { method: 'POST', json: credentials, open: true }),
  login: (credentials) => request('/api/auth/login', { method: 'POST', json: credentials, open: true }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  me: () => request('/api/auth/me'),
  changePassword: (payload) => request('/api/auth/password', { method: 'POST', json: payload }),

  listDatasources: () => request('/api/datasources'),
  deleteDatasource: (id) => request(`/api/datasources/${enc(id)}`, { method: 'DELETE' }),
  refreshDatasource: (id) => request(`/api/datasources/${enc(id)}/refresh`, { method: 'POST' }),
  testConnection: (payload) => request('/api/database/test', { method: 'POST', json: payload }),
  connectDatabase: (payload) => request('/api/database/connect', { method: 'POST', json: payload }),
  previewTable: (id, table, limit = 10) =>
    request(`/api/datasources/${enc(id)}/tables/${enc(table)}/preview?limit=${limit}`),

  previewUpload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/upload/preview', { method: 'POST', body: form })
  },
  importUpload: (payload) => request('/api/upload/import', { method: 'POST', json: payload }),

  getSchema: (id) => request(`/api/datasources/${enc(id)}/schema`),
  getBlockedColumns: (id) => request(`/api/datasources/${enc(id)}/blocked-columns`),
  setBlockedColumns: (id, columns) =>
    request(`/api/datasources/${enc(id)}/blocked-columns`, { method: 'PUT', json: { columns } }),
  suggestRelationships: (id) => request(`/api/datasources/${enc(id)}/relationships/suggestions`),
  confirmRelationship: (id, relationship) =>
    request(`/api/datasources/${enc(id)}/relationships`, { method: 'POST', json: relationship }),
  deleteRelationship: (id, relationshipId) =>
    request(`/api/datasources/${enc(id)}/relationships/${enc(relationshipId)}`, { method: 'DELETE' }),

  chat: (payload, signal) => request('/api/chat/query', { method: 'POST', json: payload, signal }),
  listConversations: (datasourceId) =>
    request(`/api/chat/conversations${datasourceId ? `?datasource_id=${enc(datasourceId)}` : ''}`),
  getConversation: (id) => request(`/api/chat/conversations/${enc(id)}`),
  deleteConversation: (id) => request(`/api/chat/conversations/${enc(id)}`, { method: 'DELETE' }),

  history: (datasourceId, { q, status, limit = 20, offset = 0 } = {}) => {
    const params = new URLSearchParams({ datasource_id: datasourceId, limit, offset })
    if (q?.trim()) params.set('q', q.trim())
    if (status) params.set('status', status)
    return request(`/api/history?${params}`)
  },
  clearHistory: (datasourceId) => request(`/api/history?datasource_id=${enc(datasourceId)}`, { method: 'DELETE' }),
  deleteTurn: (turnId) => request(`/api/history/turns/${enc(turnId)}`, { method: 'DELETE' }),
  keptResult: (turnId) => request(`/api/history/turns/${enc(turnId)}/result`),

  listContext: (datasourceId) => request(`/api/context/${enc(datasourceId)}`),
  createContext: (datasourceId, entry) =>
    request(`/api/context/${enc(datasourceId)}`, { method: 'POST', json: entry }),
  updateContext: (datasourceId, entryId, entry) =>
    request(`/api/context/${enc(datasourceId)}/${enc(entryId)}`, { method: 'PUT', json: entry }),
  deleteContext: (datasourceId, entryId) =>
    request(`/api/context/${enc(datasourceId)}/${enc(entryId)}`, { method: 'DELETE' }),
  previewContextImport: (datasourceId, file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/context/${enc(datasourceId)}/import/preview`, { method: 'POST', body: form })
  },
  importContext: (datasourceId, payload) =>
    request(`/api/context/${enc(datasourceId)}/import`, { method: 'POST', json: payload }),
  addContextExamples: (datasourceId) => request(`/api/context/${enc(datasourceId)}/examples`, { method: 'POST' }),

  execute: (datasourceId, sql) =>
    request('/api/sql/execute', { method: 'POST', json: { datasource_id: datasourceId, sql } }),
  chart: (payload) => request('/api/analytics/chart', { method: 'POST', json: payload }),
  summary: (payload, signal) => request('/api/analytics/summary', { method: 'POST', json: payload, signal }),
}
