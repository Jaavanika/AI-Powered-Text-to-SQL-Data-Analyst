// The login token for this browser. It is kept in localStorage so a login survives a reload. If storage is
// blocked (a private window, for instance) the token lasts for the page load only, held in memory.
// Storage is the source of truth whenever it works: a token removed from it (logging out in another tab)
// must not come back from memory.

const KEY = 'tsql.token'
let memoryToken = null            // used only while storage is unavailable

export function getToken() {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return memoryToken
  }
}

export function setToken(token) {
  try {
    localStorage.setItem(KEY, token)
    memoryToken = null
  } catch {
    memoryToken = token
  }
}

export function clearToken() {
  memoryToken = null
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* nothing stored */
  }
}
