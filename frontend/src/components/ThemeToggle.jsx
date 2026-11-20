import { useEffect, useState } from 'react'
import { loadSetting, saveSetting } from '../lib/workspace.js'

const THEMES = ['auto', 'light', 'dark']

export default function ThemeToggle() {
  const [theme, setTheme] = useState(() => loadSetting('theme', 'auto'))
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'auto') delete root.dataset.theme
    else root.dataset.theme = theme
    saveSetting('theme', theme === 'auto' ? null : theme)
  }, [theme])
  const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]
  return (
    <button type="button" className="btn btn--ghost btn--small" onClick={() => setTheme(next)}
      aria-label={`Colour theme: ${theme}. Switch to ${next}.`} title="Colour theme">
      {theme === 'dark' ? '☾ Dark' : theme === 'light' ? '☀ Light' : '◐ Auto'}
    </button>
  )
}
