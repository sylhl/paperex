/* 错误卡：结构化错误（标题/详情/建议/动作按钮/复制），不可自动消失 */

import { useState } from 'react'
import { copyText } from '../lib/format.js'
import { Icon } from './Icon.jsx'

const ACTION_LABELS = {
  settings: '检查设置',
  repick: '重新选择 PDF',
  retry: '重试',
  history: '返回译文库',
  doc: '返回译文库',
  copyCmd: '复制启动命令',
  copyPath: '复制字体目录',
  refresh: '刷新译文库'
}

export function ErrorCard({ error, onClose, onRetry, onSettings, onHistory, onRepick, onCopied, compact = false }) {
  const [copied, setCopied] = useState('')
  if (!error) return null

  const tone = error.tone || 'error'
  const canRetry = Boolean(error.retryable && onRetry)
  const action = error.action
  const showSettings = action === 'settings' || Boolean(onSettings && canRetry)
  const showRepick = action === 'repick' && onRepick
  const showHistory = (action === 'history' || action === 'doc') && onHistory
  const showCopy = Boolean(error.copyText) || action === 'copyCmd' || action === 'copyPath'

  const handleCopy = async (text, kind) => {
    const ok = await copyText(text)
    setCopied(ok ? kind : '')
    onCopied?.(ok, kind)
    if (ok) setTimeout(() => setCopied(''), 2000)
  }

  return (
    <div className={compact ? 'error-card error-card-inline' : 'error-card'} role="alert" aria-live="assertive">
      <div className="error-card-head">
        <span className={`error-card-icon error-card-icon-${tone}`}>
          <Icon name={tone === 'warn' ? 'AlertTriangle' : 'AlertCircle'} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="error-card-title">{error.title || '操作失败'}</h3>
          <p className="error-card-detail">{error.detail}</p>
        </div>
        {onClose && (
          <button type="button" className="icon-btn h-7 w-7 shrink-0" onClick={onClose} title="关闭">
            <Icon name="Close" className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {error.hint && (
        <p className="error-card-hint">
          <Icon name="Info" className="h-3.5 w-3.5 shrink-0" />
          <span>{error.hint}</span>
        </p>
      )}

      {error.raw && error.raw !== error.detail && (
        <details className="error-card-raw">
          <summary>原始错误信息</summary>
          <pre>{error.raw}</pre>
        </details>
      )}

      <div className="error-card-actions">
        {canRetry && (
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            <Icon name="Refresh" className="h-3.5 w-3.5" />
            {ACTION_LABELS.retry}
          </button>
        )}
        {showRepick && (
          <button type="button" className={canRetry ? 'btn' : 'btn btn-primary'} onClick={onRepick}>
            <Icon name="File" className="h-3.5 w-3.5" />
            {ACTION_LABELS.repick}
          </button>
        )}
        {showSettings && (
          <button type="button" className={canRetry || showRepick ? 'btn' : 'btn btn-primary'} onClick={onSettings}>
            <Icon name="Gear" className="h-3.5 w-3.5" />
            {ACTION_LABELS.settings}
          </button>
        )}
        {showHistory && (
          <button type="button" className="btn" onClick={onHistory}>
            <Icon name="Grid" className="h-3.5 w-3.5" />
            {ACTION_LABELS.history}
          </button>
        )}
        {showCopy && (
          <button
            type="button"
            className="btn"
            onClick={() => handleCopy(error.copyText || String(error.raw || error.detail || ''), 'copy')}
          >
            <Icon name={copied === 'copy' ? 'Check' : 'Copy'} className="h-3.5 w-3.5" />
            {copied === 'copy' ? '已复制' : action === 'copyCmd' ? ACTION_LABELS.copyCmd : action === 'copyPath' ? ACTION_LABELS.copyPath : '复制详情'}
          </button>
        )}
      </div>
      {error.status ? <span className="error-card-status">HTTP {error.status}</span> : null}
    </div>
  )
}
