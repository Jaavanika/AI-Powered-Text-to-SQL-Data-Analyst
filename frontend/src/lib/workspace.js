// The workspace id separates one browser's data from another's. It is not authentication
// (real accounts come later); it only has to be stable per browser.

const KEY = 'tsql.workspaceId'
let memoryId = null

function generate() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return 'ws-' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export function getWorkspaceId() {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved) return saved
    const id = generate()
    localStorage.setItem(KEY, id)
    return id
  } catch {
    // Storage blocked (private window, etc.): fall back to an id that lasts for this page load.
    memoryId = memoryId || generate()
    return memoryId
  }
}

export function loadSetting(key, fallback = null) {
  try {
    const value = localStorage.getItem(`tsql.${key}`)
    return value === null ? fallback : value
  } catch {
    return fallback
  }
}

export function saveSetting(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(`tsql.${key}`)
    else localStorage.setItem(`tsql.${key}`, String(value))
  } catch {
    /* ignore: settings are a convenience only */
  }
}
