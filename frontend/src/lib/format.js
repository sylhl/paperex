/* 纯格式化工具：时间、体积、ETA、QA 计数、剪贴板 */

import { QA_STATUS } from '../constants.js'

export function basename(path) {
  return String(path || '').split(/[\\/]/).filter(Boolean).pop() || 'document.pdf'
}

export function formatDate(timestamp) {
  const value = Number(timestamp)
  if (!Number.isFinite(value) || value <= 0) return '—'
  const date = new Date(value < 1e12 ? value * 1000 : value)
  const pad = (v) => String(v).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatBytes(bytes) {
  const value = Number(bytes)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function formatDuration(ms) {
  const value = Number(ms)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 1000) return `${Math.round(value)}ms`
  if (value < 60000) return `${(value / 1000).toFixed(1)}s`
  const minutes = Math.floor(value / 60000)
  const seconds = Math.round((value % 60000) / 1000)
  return `${minutes} 分 ${seconds} 秒`
}

export function formatSeconds(totalSeconds) {
  const total = Math.max(0, Math.round(Number(totalSeconds) || 0))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  if (minutes <= 0) return `${seconds}s`
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
}

export function formatEta(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return ''
  if (value < 60) return `预计剩余不到 1 分钟`
  const minutes = Math.round(value / 60)
  if (minutes < 60) return `预计剩余 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  return `预计剩余 ${hours} 小时 ${minutes % 60} 分`
}

export function formatCount(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '0'
  return String(Math.max(0, Math.round(number)))
}

export function qaCounts(qa) {
  const source = qa || {}
  const errors = Number(source.errors || 0)
  const warnings = Number(source.warnings || 0)
  const total = Number(source.total ?? errors + warnings)
  const skipped = Boolean(source.skipped || source.status === 'skipped' || source.status === 'not_run')
  const status = String(source.status || (errors ? 'failed' : warnings ? 'warnings' : skipped ? 'skipped' : 'passed'))
  return { errors, warnings, total, skipped, status, tone: (QA_STATUS[status] || QA_STATUS.passed).tone }
}

export function qaTone(qa) {
  const { skipped, errors, warnings } = qaCounts(qa)
  if (skipped) return 'bg-zinc-500'
  return errors ? 'bg-rose-400' : warnings ? 'bg-amber-400' : 'bg-mint-400'
}

export function qaToneText(qa) {
  const { skipped, errors, warnings } = qaCounts(qa)
  if (skipped) return 'text-zinc-400'
  return errors ? 'text-rose-300' : warnings ? 'text-amber-300' : 'text-mint-300'
}

export function safeJson(value, indent = 2) {
  try {
    return JSON.stringify(value, null, indent)
  } catch {
    return String(value)
  }
}

export async function copyText(text) {
  const value = String(text == null ? '' : text)
  if (!value) return false
  try {
    await navigator.clipboard.writeText(value)
    return true
  } catch {
    try {
      const area = document.createElement('textarea')
      area.value = value
      area.setAttribute('readonly', '')
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(area)
      return ok
    } catch {
      return false
    }
  }
}

export function truncateMiddle(text, max = 48) {
  const value = String(text || '')
  if (value.length <= max) return value
  const half = Math.floor((max - 1) / 2)
  return `${value.slice(0, half)}…${value.slice(value.length - half)}`
}

/* 从阶段进度估算 ETA：优先后端 eta_seconds，缺失时客户端平滑 */
export function estimateEta({ pct, lastPct, lastAt, now, rateRef }) {
  const current = Number(pct)
  if (!Number.isFinite(current) || current <= 0 || current >= 100) return null
  const elapsed = Math.max(0.001, (now - Number(lastAt || now)) / 1000)
  const delta = Math.max(0, current - Number(lastPct || 0))
  const instant = (delta / elapsed) * 60 // 百分比 / 分钟
  const previous = Number(rateRef.current) || 0
  const rate = Math.max(0.35, previous ? previous * 0.8 + instant * 0.2 : instant || 0.35)
  rateRef.current = rate
  return Math.round(((100 - current) / rate) * 60)
}
