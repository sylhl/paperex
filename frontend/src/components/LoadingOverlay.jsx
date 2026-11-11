/* 转换进度遮罩：阶段、百分比、ETA、质量轮次、队列位次、日志、取消 */

import { useEffect, useRef, useState } from 'react'
import { LOADING_LINES, STAGE_LABELS, STAGE_STEPS, STAGE_STEP_INDEX, QUALITY_MODES } from '../constants.js'
import { clamp } from '../lib/storage.js'
import { estimateEta, formatEta, formatSeconds } from '../lib/format.js'
import { Icon } from './Icon.jsx'

function stageIndexOf(job) {
  const status = String(job?.status || '').toLowerCase()
  if (status === 'queued') return -1
  if (status === 'done') return STAGE_STEPS.length
  if (status === 'failed' || status === 'error' || status === 'canceled' || status === 'cancelled') return STAGE_STEPS.length
  const index = STAGE_STEP_INDEX[job?.stage]
  return Number.isFinite(index) ? index : 0
}

function qualityLabel(mode) {
  return QUALITY_MODES.find((item) => item.id === mode)?.name || ''
}

export function LoadingOverlay({ job, fileName, startedAt, onCancel, cancelState = 'idle', demo = false }) {
  const [elapsed, setElapsed] = useState(0)
  const [line] = useState(() => LOADING_LINES[Math.floor(Math.random() * LOADING_LINES.length)])
  const [showLog, setShowLog] = useState(false)
  const rateRef = useRef(0)
  const lastPctRef = useRef(-1)
  const lastAtRef = useRef(Date.now())
  const [eta, setEta] = useState(null)

  useEffect(() => {
    const started = Number(startedAt) || Date.now()
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [startedAt])

  const status = String(job?.status || 'uploading').toLowerCase()
  const rawProgress = Number(job?.progress)
  const pct = Number.isFinite(rawProgress)
    ? clamp(Math.round(rawProgress), 0, 100)
    : status === 'uploading'
      ? 0
      : 0
  const stage = job?.stage || (status === 'queued' ? 'queued' : 'parse')
  const stageLabel =
    job?.stage_label ||
    STAGE_LABELS[stage] ||
    (status === 'queued' ? '排队中' : status === 'uploading' ? '上传中' : '转换中')
  const stepIndex = stageIndexOf(job)
  const pages = Number(job?.pages_total) > 0 ? `${job.pages_done || 0}/${job.pages_total}` : ''
  const queuePosition = Number(job?.queue_position) || 0
  const round = Number(job?.quality_round) || 0
  const modeName = qualityLabel(job?.quality_mode)
  const canCancel = job ? job.can_cancel !== false && status !== 'canceled' && status !== 'cancelled' : false

  useEffect(() => {
    if (!job) return
    if (Number.isFinite(rawProgress) && rawProgress > 0 && rawProgress < 100) {
      const now = Date.now()
      const next = estimateEta({
        pct: rawProgress,
        lastPct: lastPctRef.current < 0 ? 0 : lastPctRef.current,
        lastAt: lastAtRef.current,
        now,
        rateRef
      })
      lastPctRef.current = rawProgress
      lastAtRef.current = now
      if (next) setEta(next)
    }
  }, [job, rawProgress])

  const etaText =
    Number(job?.eta_seconds) > 0
      ? formatEta(job.eta_seconds)
      : eta
        ? formatEta(eta)
        : pct > 0
          ? '正在估算剩余时间…'
          : ''

  const logLines = Array.isArray(job?.log) ? job.log.slice(-5) : []
  const cancelling = cancelState === 'cancelling'

  return (
    <div className="loading-overlay" role="status" aria-live="polite" aria-label="转换进度">
      <div className="loading-card">
        <div className="loading-head">
          <span className="loading-mark">
            <span className="loading-ring" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="loading-eyebrow">{demo ? '演示模式 · 转换中' : '正在转换'}</p>
            <h2 className="loading-title">{fileName || '未命名.pdf'}</h2>
          </div>
          {canCancel && (
            <button
              type="button"
              className="btn loading-cancel"
              onClick={onCancel}
              disabled={cancelling}
            >
              <Icon name={cancelling ? 'Refresh' : 'Ban'} className={cancelling ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
              {cancelling ? '正在取消…' : '取消转换'}
            </button>
          )}
        </div>

        <div className="loading-stage">
          <span className="loading-stage-label">{stageLabel}</span>
          <span className="loading-stage-pct">{pct}%</span>
        </div>

        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>

        <div className="loading-meta">
          {queuePosition > 0 && (
            <span className="loading-chip">
              <Icon name="Queue" className="h-3 w-3" /> 排队第 {queuePosition} 位
            </span>
          )}
          {pages && (
            <span className="loading-chip">
              <Icon name="File" className="h-3 w-3" /> 第 {pages} 页
            </span>
          )}
          {round > 0 && (
            <span className="loading-chip">
              <Icon name="Shield" className="h-3 w-3" /> 第 {round} 轮质检
            </span>
          )}
          {modeName && <span className="loading-chip">质量 {modeName}</span>}
          <span className="loading-chip">
            <Icon name="Clock" className="h-3 w-3" /> 已用 {formatSeconds(elapsed)}
          </span>
          {etaText && <span className="loading-chip loading-chip-eta">{etaText}</span>}
        </div>

        <ol className="stage-steps">
          {STAGE_STEPS.map((step, index) => {
            const state = stepIndex > index ? 'done' : stepIndex === index ? 'active' : 'todo'
            return (
              <li key={step.id} className={`stage-step stage-step-${state}`} title={step.hint}>
                <span className="stage-dot">{state === 'done' ? <Icon name="Check" className="h-2.5 w-2.5" /> : null}</span>
                <span className="stage-step-label">{step.label}</span>
              </li>
            )
          })}
        </ol>

        <p className="loading-line">{line}</p>

        {logLines.length > 0 && (
          <div className="loading-log">
            <button type="button" className="loading-log-toggle" onClick={() => setShowLog((prev) => !prev)}>
              <Icon name={showLog ? 'ChevronDown' : 'ChevronRight'} className="h-3 w-3" />
              最近日志（{logLines.length}）
            </button>
            {showLog && <pre>{logLines.join('\n')}</pre>}
          </div>
        )}

        <p className="loading-foot">转换过程中可以继续使用界面；取消后不会留下半成品文件。</p>
      </div>
    </div>
  )
}
