/* localStorage 读写、旧键迁移、主题/模式/设置/最近文档加载 */

import {
  DEFAULT_SETTINGS,
  LEGACY_STORAGE_KEYS,
  STORAGE_KEYS,
  THEMES
} from '../constants.js'

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

/* ?apiBase= 的一次性覆盖：只写 sessionStorage，刷新仍生效但关掉标签页即失效，不污染 localStorage */
export const API_OVERRIDE_KEY = 'paperex.apiBase.override'

export const isTauri =
  typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__)

export function readStored(key, fallback) {
  try {
    const value = localStorage.getItem(key)
    return value == null ? fallback : value
  } catch {
    return fallback
  }
}

export function writeStored(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

export function removeStored(key) {
  try {
    localStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}

export function migrateLegacyStorage() {
  try {
    for (const [name, key] of Object.entries(STORAGE_KEYS)) {
      const legacyKey = LEGACY_STORAGE_KEYS[name]
      const legacyValue = localStorage.getItem(legacyKey)
      if (legacyValue == null) continue
      if (localStorage.getItem(key) == null) localStorage.setItem(key, legacyValue)
      localStorage.removeItem(legacyKey)
    }
  } catch {
    /* ignore */
  }
}

export function getApiBase() {
  try {
    const override = sessionStorage.getItem(API_OVERRIDE_KEY)
    if (override) return override.replace(/\/+$/, '')
    return (localStorage.getItem(STORAGE_KEYS.apiBase) || 'http://127.0.0.1:8756').replace(/\/+$/, '')
  } catch {
    return 'http://127.0.0.1:8756'
  }
}

export function setApiBase(value) {
  const next = String(value || '').trim().replace(/\/+$/, '')
  if (!next) {
    removeStored(STORAGE_KEYS.apiBase)
    return 'http://127.0.0.1:8756'
  }
  writeStored(STORAGE_KEYS.apiBase, next)
  return next
}

export function loadTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEYS.theme)
    if (saved && THEMES.some((theme) => theme.id === saved)) return saved
  } catch {
    /* ignore */
  }
  return 'mint'
}

export function loadMode() {
  try {
    const saved = localStorage.getItem(STORAGE_KEYS.mode)
    if (saved === 'light' || saved === 'dark') return saved
    // 旧版「纸白」主题迁移为浅色模式
    if (localStorage.getItem(STORAGE_KEYS.theme) === 'paper') return 'light'
  } catch {
    /* ignore */
  }
  return 'dark'
}

export function loadSettings() {
  let stored = {}
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.settings)
    if (raw) stored = JSON.parse(raw) || {}
  } catch {
    stored = {}
  }
  const merged = { ...DEFAULT_SETTINGS, ...stored }
  // 旧版本没有质量档位字段：按设置里的 qa 开关推断
  if (stored.qualityMode == null) {
    merged.qualityMode = stored.qaMode === 'off' ? 'fast' : DEFAULT_SETTINGS.qualityMode
  }
  if (typeof merged.thinking !== 'boolean') merged.thinking = false
  if (typeof merged.useCache !== 'boolean') merged.useCache = true
  return merged
}

export function saveSettings(settings) {
  writeStored(STORAGE_KEYS.settings, JSON.stringify(settings))
}

export function loadLastDoc() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.lastDoc)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed?.original || !parsed?.translated) return null
    return parsed
  } catch {
    return null
  }
}

export function saveLastDoc(doc) {
  if (!doc) {
    removeStored(STORAGE_KEYS.lastDoc)
    return
  }
  writeStored(STORAGE_KEYS.lastDoc, JSON.stringify(doc))
}
