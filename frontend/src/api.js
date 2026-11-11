/* 后端 API 封装：所有请求集中在这里，URL 动态读取 localStorage / ?apiBase= 覆盖 */

import { getApiBase } from './lib/storage.js'
import { errorFromResponse } from './lib/errors.js'

export class ApiError extends Error {
  constructor(message, { status = 0, data = null, code = '' } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
    this.code = code
  }
}

const NETWORK_MESSAGE = 'Failed to fetch：无法连接本地后端'

async function request(path, { method = 'GET', body, headers, signal, timeoutMs = 0 } = {}) {
  const url = `${getApiBase()}${path}`
  const controller = new AbortController()
  let timer = null
  if (timeoutMs > 0) {
    timer = setTimeout(() => controller.abort(), timeoutMs)
  }
  const onAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onAbort, { once: true })
  }
  try {
    const response = await fetch(url, {
      method,
      headers,
      body,
      signal: controller.signal,
      cache: 'no-store'
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      throw errorFromResponse(response.status, data, `HTTP ${response.status}`)
    }
    return data
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new ApiError('请求已取消', { code: 'aborted' })
    }
    if (error && error.status) throw error
    throw new ApiError(NETWORK_MESSAGE, { code: 'backend_unreachable' })
  } finally {
    if (timer) clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', onAbort)
  }
}

function jsonHeaders() {
  return { 'Content-Type': 'application/json' }
}

export const api = {
  health() {
    return request('/api/health', { timeoutMs: 4000 })
  },

  createJob(payload) {
    return request('/api/jobs', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
      timeoutMs: 30000
    })
  },

  getJob(jobId) {
    return request(`/api/jobs/${encodeURIComponent(jobId)}`)
  },

  cancelJob(jobId) {
    return request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
      timeoutMs: 10000
    })
  },

  getJobQa(jobId) {
    return request(`/api/jobs/${encodeURIComponent(jobId)}/qa`)
  },

  getModels() {
    return request('/api/models', { timeoutMs: 8000 })
  },

  testConnection(payload) {
    return request('/api/test-connection', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
      timeoutMs: 45000
    })
  },

  async uploadFile(file) {
    const url = `${getApiBase()}/api/upload`
    const form = new FormData()
    form.append('file', file, file.name || 'upload.pdf')
    let response
    try {
      /* 不要手动设置 Content-Type：浏览器需要自己生成 multipart boundary */
      response = await fetch(url, { method: 'POST', body: form })
    } catch {
      throw new ApiError(NETWORK_MESSAGE, { code: 'backend_unreachable' })
    }
    const data = await response.json().catch(() => null)
    if (!response.ok) throw errorFromResponse(response.status, data, `上传失败（HTTP ${response.status}）`)
    return data
  },

  listHistory() {
    return request('/api/history')
  },

  deleteHistory(jobId) {
    return request(`/api/history/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
  },

  deleteJob(jobId) {
    return request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
  }
}

/* 相对文件 URL → 绝对 URL（后端 files.original_url 形如 /api/files/...） */
export function absUrl(url) {
  const value = String(url || '')
  if (!value) return ''
  if (/^https?:\/\//i.test(value) || value.startsWith('blob:') || value.startsWith('data:')) return value
  return `${getApiBase()}${value.startsWith('/') ? '' : '/'}${value}`
}

/* 下载链接：统一加 ?download=1（后端 Content-Disposition: attachment） */
export function downloadUrl(url) {
  const absolute = absUrl(url)
  if (!absolute) return ''
  if (/[?&]download=1/.test(absolute)) return absolute
  return `${absolute}${absolute.includes('?') ? '&' : '?'}download=1`
}

export function fileUrl(relativePath) {
  const value = String(relativePath || '')
  if (!value) return ''
  return absUrl(`/api/files/${value.replace(/^\/+/, '')}`)
}
