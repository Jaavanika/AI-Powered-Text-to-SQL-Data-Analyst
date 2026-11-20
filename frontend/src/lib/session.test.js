import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearToken, getToken, setToken } from './session.js'

afterEach(() => { clearToken(); vi.restoreAllMocks() })

describe('login token storage', () => {
  it('keeps the token across reads and forgets it when cleared', () => {
    expect(getToken()).toBeNull()
    setToken('abc')
    expect(getToken()).toBe('abc')
    expect(localStorage.getItem('tsql.token')).toBe('abc')
    clearToken()
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('tsql.token')).toBeNull()
  })

  it('does not bring back a token that was removed from storage, for instance by logging out in another tab', () => {
    setToken('abc')
    localStorage.removeItem('tsql.token')
    expect(getToken()).toBeNull()
  })

  it('still works for this page load when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
    setToken('in-memory')
    expect(getToken()).toBe('in-memory')
    clearToken()
    expect(getToken()).toBeNull()
  })
})
