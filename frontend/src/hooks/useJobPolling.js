/* 任务轮询：指数退避、网络错误可恢复、progress 单调、取消支持、终态回调 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { normalizeError } from '../lib/errors.js'

const MAX_FAILS = 20
const TERMINAL_DONE = new Set(['done', 'completed', 'success', 'succeeded'])
const TERMINAL_FAILED = new Set(['failed', 'error'])
const TERMINAL_CANCELED = new Set(['canceled', 'cancelled', 'cancelled_by_user'])

function isDone(status) {
  return TERMINAL_DONE.has(String(status || '').toLowerCase())
}
function isFailed(status) {
  return TERMINAL_FAILED.has(String(status || '').toLowerCase())
}
function isCanceled(status) {
  return TERMINAL_CANCELED.has(String(status || '').toLowerCase())
}

export function useJobPolling(jobId, options = {}) {
  const { interval = 700, onUpdate, onDone, onError, onCanceled } = options
  const [job, setJob] = useState(null)
  const [connected, setConnected] = useState(true)
  const [cancelState, setCancelState] = useState('idle')

  const callbacksRef = useRef({ onUpdate, onDone, onError, onCanceled })
  callbacksRef.current = { onUpdate, onDone, onError, onCanceled }

  const stateRef = useRef({ stopped: false, fails: 0, timer: null, lastProgress: -1, jobId: null, cancelRequested: false })
  const jobRef = useRef(null)

  const stop = useCallback(() => {
    const state = stateRef.current
    state.stopped = true
    if (state.timer) {
      clearTimeout(state.timer)
      state.timer = null
    }
  }, [])

  useEffect(() => {
    const state = stateRef.current
    state.stopped = false
    state.fails = 0
    state.lastProgress = -1
    state.jobId = jobId
    state.cancelRequested = false
    setJob(null)
    jobRef.current = null
    setConnected(true)
    setCancelState('idle')
    if (!jobId) return undefined

    const schedule = (fn, delay) => {
      state.timer = setTimeout(fn, delay)
    }

    const poll = async () => {
      if (state.stopped || state.jobId !== jobId) return
      try {
        const data = await api.getJob(jobId)
        if (state.stopped || state.jobId !== jobId) return
        state.fails = 0
        setConnected(true)
        const rawProgress = Number(data?.progress)
        let progress = data?.progress
        if (Number.isFinite(rawProgress)) {
          const value = Math.max(state.lastProgress, Math.min(100, Math.max(0, rawProgress)))
          state.lastProgress = value
          progress = value
        }
        const next = { ...data, job_id: data?.job_id || jobId, progress }
        jobRef.current = next
        setJob(next)
        callbacksRef.current.onUpdate?.(next)
        const status = next.status
        if (isDone(status)) {
          state.stopped = true
          callbacksRef.current.onDone?.(next)
          return
        }
        if (isFailed(status)) {
          state.stopped = true
          const error = normalizeError(next.error || '转换失败', { context: 'job' })
          callbacksRef.current.onError?.(error, next)
          return
        }
        if (isCanceled(status)) {
          state.stopped = true
          setCancelState('cancelled')
          callbacksRef.current.onCanceled?.(next)
          return
        }
      } catch (error) {
        if (state.stopped || state.jobId !== jobId) return
        if (error?.status === 404) {
          state.stopped = true
          callbacksRef.current.onError?.(
            normalizeError(error?.data || error, { context: 'job', status: 404 }),
            jobRef.current
          )
          return
        }
        state.fails += 1
        setConnected(false)
        if (state.fails >= MAX_FAILS) {
          state.stopped = true
          callbacksRef.current.onError?.(normalizeError(error, { context: 'network' }), jobRef.current)
          return
        }
      }
      if (state.stopped) return
      const delay = state.fails ? Math.min(800 * 2 ** state.fails, 10000) : interval
      schedule(poll, delay)
    }

    schedule(poll, 0)

    return () => {
      state.stopped = true
      if (state.timer) {
        clearTimeout(state.timer)
        state.timer = null
      }
    }
  }, [jobId, interval])

  const cancel = useCallback(async () => {
    if (!jobId) return null
    setCancelState('cancelling')
    stateRef.current.cancelRequested = true
    try {
      const result = await api.cancelJob(jobId)
      const status = String(result?.status || '')
      if (isCanceled(status) || isDone(status) || isFailed(status)) {
        setCancelState('cancelled')
      }
      return result
    } catch (error) {
      setCancelState('failed')
      throw error
    }
  }, [jobId])

  return { job, connected, cancelState, cancel, stop }
}
