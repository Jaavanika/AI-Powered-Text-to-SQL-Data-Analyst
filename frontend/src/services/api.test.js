import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, messageFromDetail } from './api.js'

function respond(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300, status,
    json: () => (body === undefined ? Promise.reject(new Error('no body')) : Promise.resolve(body)),
  })
}

let fetchMock
beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

describe('requests', () => {
  it('sends the workspace header on every call, and the same one each time', async () => {
    fetchMock.mockImplementation(() => respond(200, []))
    await api.listDatasources()
    await api.listConversations('sample')
    const ids = fetchMock.mock.calls.map(([, init]) => init.headers['X-Workspace-Id'])
    expect(ids[0]).toMatch(/^ws-[0-9a-f]{32}$/)          // valid for the backend's 8-64 character rule
    expect(ids[1]).toBe(ids[0])
  })

  it('sends JSON bodies with a content type', async () => {
    fetchMock.mockImplementation(() => respond(200, {}))
    await api.chat({ datasource_id: 'sample', message: 'hi' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/chat/query')
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual({ datasource_id: 'sample', message: 'hi' })
  })

  it('uploads files as multipart without forcing a content type', async () => {
    fetchMock.mockImplementation(() => respond(200, {}))
    const file = new File(['a,b\n1,2'], 'x.csv', { type: 'text/csv' })
    await api.previewUpload(file)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/upload/preview')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.body.get('file').name).toBe('x.csv')
    expect(init.headers['Content-Type']).toBeUndefined()    // the browser must add the multipart boundary
  })

  it('encodes ids and table names in paths', async () => {
    fetchMock.mockImplementation(() => respond(200, {}))
    await api.previewTable('a b', 'we/ird', 5)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/datasources/a%20b/tables/we%2Fird/preview?limit=5')
  })

  it('returns null for 204 responses', async () => {
    fetchMock.mockImplementation(() => respond(204))
    expect(await api.deleteDatasource('x')).toBeNull()
  })
})

describe('errors', () => {
  it('turns every shape of FastAPI error into a readable message', () => {
    expect(messageFromDetail('Data source not found.', 'x')).toBe('Data source not found.')
    expect(messageFromDetail({ message: 'The SQL failed validation.', errors: ['Only SELECT.', 'Unknown table.'] }, 'x'))
      .toBe('The SQL failed validation. Only SELECT. Unknown table.')
    expect(messageFromDetail([{ msg: 'Field required' }, { msg: 'Too long' }], 'x')).toBe('Field required; Too long')
    expect(messageFromDetail(null, 'fallback')).toBe('fallback')
    expect(messageFromDetail({}, 'fallback')).toBe('fallback')
  })

  it('throws ApiError with status and message', async () => {
    fetchMock.mockImplementation(() => respond(409, { detail: 'Table(s) already exist: customers.' }))
    const error = await api.importUpload({}).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.message).toBe('Table(s) already exist: customers.')
  })

  it('has a fallback when the error has no JSON body', async () => {
    fetchMock.mockImplementation(() => respond(502))
    const error = await api.health().catch((e) => e)
    expect(error.message).toBe('Request failed (502).')
  })

  it('explains a network failure instead of leaking the browser message', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await api.health().catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.message).toMatch(/backend running/)
  })

  it('lets aborts through unchanged so callers can tell them apart', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'))
    const error = await api.chat({}, new AbortController().signal).catch((e) => e)
    expect(error.name).toBe('AbortError')
  })
})

describe('login token', () => {
  const header = (call) => fetchMock.mock.calls[call][1].headers

  it('is sent as a bearer token once there is one, and not before', async () => {
    fetchMock.mockImplementation(() => respond(200, []))
    await api.listDatasources()
    expect(header(0).Authorization).toBeUndefined()
    localStorage.setItem('tsql.token', 'tok-123')
    await api.listDatasources()
    expect(header(1).Authorization).toBe('Bearer tok-123')
  })

  it('is not sent when logging in or registering, so an old token cannot interfere', async () => {
    localStorage.setItem('tsql.token', 'old-token')
    fetchMock.mockImplementation(() => respond(200, { token: 't', user: {} }))
    await api.login({ username: 'a', password: 'b' })
    await api.register({ username: 'a', password: 'b' })
    await api.authConfig()
    for (const call of [0, 1, 2]) expect(header(call).Authorization).toBeUndefined()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/auth/login')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ username: 'a', password: 'b' })
  })

  it('tells the app when the server rejects the token', async () => {
    localStorage.setItem('tsql.token', 'tok-123')
    const handler = vi.fn()
    const stop = api.onUnauthorized(handler)
    fetchMock.mockImplementation(() => respond(401, { detail: 'Your session has expired. Log in again.' }))
    await expect(api.listDatasources()).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledWith('Your session has expired. Log in again.')
    stop()
  })

  it('does not treat a wrong password at login as a session that ended', async () => {
    localStorage.setItem('tsql.token', 'tok-123')
    const handler = vi.fn()
    const stop = api.onUnauthorized(handler)
    fetchMock.mockImplementation(() => respond(401, { detail: 'Incorrect username or password.' }))
    await expect(api.login({ username: 'a', password: 'b' })).rejects.toMatchObject({ message: 'Incorrect username or password.' })
    expect(handler).not.toHaveBeenCalled()
    stop()
  })

  it('does not report a 401 when no token was sent', async () => {
    const handler = vi.fn()
    const stop = api.onUnauthorized(handler)
    fetchMock.mockImplementation(() => respond(401, { detail: 'Log in to continue.' }))
    await expect(api.listDatasources()).rejects.toThrow('Log in to continue.')
    expect(handler).not.toHaveBeenCalled()
    stop()
  })

  it('shows the server\'s wait message when asked to slow down', async () => {
    fetchMock.mockImplementation(() => respond(429, { detail: 'You are asking too fast. Try again in 9 seconds.' }))
    await expect(api.chat({ datasource_id: 'sample', message: 'hi' })).rejects.toMatchObject({
      status: 429, message: 'You are asking too fast. Try again in 9 seconds.' })
  })

  it('stops reporting once the handler is removed', async () => {
    localStorage.setItem('tsql.token', 'tok-123')
    const handler = vi.fn()
    api.onUnauthorized(handler)()
    fetchMock.mockImplementation(() => respond(401, { detail: 'x' }))
    await expect(api.listDatasources()).rejects.toThrow()
    expect(handler).not.toHaveBeenCalled()
  })
})
