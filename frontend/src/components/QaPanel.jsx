/* QA 报告：底栏徽标 + 抽屉（分组、搜索、页码跳转、原始 JSON） */

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { QA_CODE_LABELS, QA_STATUS } from '../constants.js'
import { DEMO_QA_REPORT } from '../demo.js'
import { normalizeError } from '../lib/errors.js'
import { copyText, qaCounts, safeJson } from '../lib/format.js'
import { Icon } from './Icon.jsx'

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 }

function severityOf(issue) {
  const value = String(issue?.severity || 'warning').toLowerCase()
  if (['error', 'critical', 'fatal', 'high'].includes(value)) return 'error'
  if (['info', 'note', 'low'].includes(value)) return 'info'
  return 'warning'
}

export function QaBadge({ qa, onClick, title = '查看译后质检报告' }) {
  const counts = qaCounts(qa)
  const label = counts.skipped
    ? 'QA 未运行'
    : counts.errors
      ? `QA ${counts.errors} 错误 · ${counts.warnings} 提醒`
      : counts.warnings
        ? `QA ${counts.warnings} 提醒`
        : qa
          ? 'QA 通过'
          : 'QA 未运行'
  return (
    <button type="button" className="qa-badge" onClick={onClick} title={title} aria-label={title}>
      <span className={`qa-dot ${qaCounts(qa).skipped ? 'bg-zinc-500' : counts.errors ? 'qa-dot-error' : counts.warnings ? 'qa-dot-warn' : 'qa-dot-ok'}`} />
      <span className="qa-badge-text">{label}</span>
      {qa ? <Icon name="ChevronRight" className="h-3 w-3 opacity-60" /> : null}
    </button>
  )
}

function normalizeReport(source) {
  const score = source?.score || {}
  const errors = Number(score.errors ?? source?.errors ?? 0)
  const warnings = Number(score.warnings ?? source?.warnings ?? 0)
  const total = Number(score.total ?? source?.total ?? errors + warnings)
  const issues = Array.isArray(source?.issues) ? source.issues : []
  const skipped = Boolean(source?.skipped || source?.status === 'skipped' || source?.status === 'not_run')
  const status = String(source?.status || (errors ? 'failed' : warnings || issues.some((i) => severityOf(i) === 'warning') ? 'warnings' : skipped ? 'skipped' : 'passed'))
  return {
    errors,
    warnings,
    total,
    issues,
    skipped,
    status,
    passes: Number(source?.passes_run ?? (Array.isArray(source?.pass_history) ? source.pass_history.length : 0)) || 0,
    repairAttempted: Boolean(source?.repair_attempted),
    score,
    bestScore: source?.best_score,
    raw: source
  }
}

export function QaPanel({ open, onClose, jobId, inlineQa, onGotoPage }) {
  const [report, setReport] = useState(null)
  const [state, setState] = useState('idle') // idle | loading | ready | fallback | error
  const [loadError, setLoadError] = useState(null)
  const [tab, setTab] = useState('all')
  const [query, setQuery] = useState('')
  const [copied, setCopied] = useState(false)
  const closeRef = useRef(null)

  useEffect(() => {
    if (!open) return
    setTab('all')
    setQuery('')
    const demoJob = String(jobId || '').startsWith('demo')
    if (demoJob) {
      setReport(DEMO_QA_REPORT)
      setState('ready')
      return
    }
    if (!jobId) {
      setReport(inlineQa || null)
      setState(inlineQa ? 'fallback' : 'ready')
      return
    }
    let alive = true
    setState('loading')
    setLoadError(null)
    api
      .getJobQa(jobId)
      .then((data) => {
        if (!alive) return
        setReport(data)
        setState('ready')
      })
      .catch((error) => {
        if (!alive) return
        if (error?.status === 404) {
          setReport(inlineQa || null)
          setState(inlineQa ? 'fallback' : 'ready')
        } else {
          setLoadError(normalizeError(error, { context: 'job' }))
          setReport(inlineQa || null)
          setState(inlineQa ? 'fallback' : 'error')
        }
      })
    return () => {
      alive = false
    }
  }, [open, jobId, inlineQa])

  useEffect(() => {
    if (open) closeRef.current?.focus()
  }, [open])

  const data = useMemo(() => normalizeReport(report), [report])

  const visibleIssues = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return data.issues
      .filter((issue) => (tab === 'all' ? true : severityOf(issue) === tab))
      .filter((issue) => {
        if (!needle) return true
        const haystack = `${issue.code || ''} ${issue.message || ''} ${issue.page ?? ''} ${QA_CODE_LABELS[issue.code] || ''}`.toLowerCase()
        return haystack.includes(needle)
      })
      .sort((a, b) => (SEVERITY_ORDER[severityOf(a)] ?? 1) - (SEVERITY_ORDER[severityOf(b)] ?? 1) || (a.page || 0) - (b.page || 0))
  }, [data.issues, tab, query])

  const groups = useMemo(() => {
    const map = new Map()
    visibleIssues.forEach((issue) => {
      const code = String(issue.code || 'other')
      if (!map.has(code)) map.set(code, [])
      map.get(code).push(issue)
    })
    return Array.from(map.entries()).map(([code, issues]) => ({ code, issues }))
  }, [visibleIssues])

  const errorCount = data.issues.filter((i) => severityOf(i) === 'error').length
  const warningCount = data.issues.filter((i) => severityOf(i) === 'warning').length
  const statusInfo = QA_STATUS[data.status] || QA_STATUS.passed

  if (!open) return null

  const handleCopy = async () => {
    const ok = await copyText(safeJson(data.raw || {}, 2))
    setCopied(ok)
    if (ok) setTimeout(() => setCopied(false), 2000)
  }

  return (
    <>
      <div className="qa-panel-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="qa-panel" role="dialog" aria-modal="true" aria-label="译后质检">
        <header className="qa-panel-head">
          <div className="flex items-center gap-2">
            <span className="qa-panel-icon">
              <Icon name="Shield" className="h-4 w-4" />
            </span>
            <div>
              <h2 className="qa-panel-title">译后质检</h2>
              <p className="qa-panel-sub">{jobId ? `任务 ${String(jobId).slice(0, 8)}` : '当前译文'}</p>
            </div>
          </div>
          <button ref={closeRef} type="button" className="icon-btn h-7 w-7" onClick={onClose} aria-label="关闭质检报告">
            <Icon name="Close" className="h-3.5 w-3.5" />
          </button>
        </header>

        <div className="qa-panel-body">
          {state === 'loading' && (
            <div className="qa-state">
              <span className="loading-spinner" />
              <p>正在读取 QA 报告…</p>
            </div>
          )}

          {state === 'error' && (
            <div className="qa-state">
              <Icon name="AlertCircle" className="h-5 w-5" />
              <p>{loadError?.title || 'QA 报告不可用'}</p>
              <p className="qa-state-sub">{loadError?.detail || '可以稍后重试，或回看内联摘要。'}</p>
            </div>
          )}

          {state !== 'loading' && state !== 'error' && (
            <>
              <div className={`qa-summary qa-summary-${statusInfo.tone}`}>
                <div className="qa-summary-top">
                  <span className={`qa-status qa-status-${statusInfo.tone}`}>{statusInfo.label}</span>
                  <span className="qa-summary-total">共 {data.total} 条</span>
                </div>
                <div className="qa-summary-counts">
                  <span className="qa-count qa-error">
                    <Icon name="AlertCircle" className="h-3 w-3" /> 错误 {data.errors}
                  </span>
                  <span className="qa-count qa-warn">
                    <Icon name="AlertTriangle" className="h-3 w-3" /> 提醒 {data.warnings}
                  </span>
                  {data.passes > 0 && <span className="qa-count">质检 {data.passes} 轮</span>}
                  {data.repairAttempted && <span className="qa-count">已自动修复</span>}
                </div>
                {data.bestScore && (
                  <p className="qa-summary-best">
                    最佳轮次：错误 {data.bestScore.errors ?? 0} / 提醒 {data.bestScore.warnings ?? 0}
                  </p>
                )}
              </div>

              {state === 'fallback' && (
                <p className="qa-note">
                  <Icon name="Info" className="h-3.5 w-3.5 shrink-0" />
                  完整报告尚未生成或接口不可用，这里展示的是任务摘要。
                </p>
              )}

              <div className="qa-tabs" role="tablist" aria-label="质检问题分类">
                {[
                  { id: 'all', label: '全部', count: data.issues.length || data.total },
                  { id: 'error', label: '错误', count: errorCount },
                  { id: 'warning', label: '提醒', count: warningCount }
                ].map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === item.id}
                    className={`qa-tab ${tab === item.id ? 'qa-tab-active' : ''}`}
                    onClick={() => setTab(item.id)}
                  >
                    {item.label}
                    <span className="qa-tab-count">{item.count}</span>
                  </button>
                ))}
              </div>

              <div className="qa-search">
                <Icon name="Search" className="h-3.5 w-3.5" />
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索说明、问题代码或页码…"
                  aria-label="搜索质检问题"
                />
                {query && (
                  <button type="button" className="icon-btn h-6 w-6" onClick={() => setQuery('')} aria-label="清除搜索">
                    <Icon name="Close" className="h-3 w-3" />
                  </button>
                )}
              </div>

              {data.skipped && data.issues.length === 0 ? (
                <div className="qa-state">
                  <Icon name="Info" className="h-5 w-5" />
                  <p>译后质检未运行</p>
                  <p className="qa-state-sub">该任务使用了质量模式「关闭」，或旧版本未记录报告（PAPEREX_POST_QA=0）。</p>
                </div>
              ) : groups.length === 0 ? (
                <div className="qa-state">
                  <Icon name="Check" className="h-5 w-5" />
                  <p>{query || tab !== 'all' ? '没有匹配的问题' : data.total === 0 ? '未发现问题。' : '报告只提供了汇总，没有明细条目。'}</p>
                  {data.total > 0 && data.issues.length === 0 && (
                    <p className="qa-state-sub">错误 {data.errors} 条、提醒 {data.warnings} 条。重新运行一次转换可以拿到完整明细。</p>
                  )}
                </div>
              ) : (
                <div className="qa-groups">
                  {groups.map((group) => (
                    <section key={group.code} className="qa-group">
                      <h3 className="qa-group-head">
                        <span className="qa-group-code">{group.code}</span>
                        <span className="qa-group-label">{QA_CODE_LABELS[group.code] || '其他问题'}</span>
                        <span className="qa-group-count">{group.issues.length}</span>
                      </h3>
                      <ul className="qa-issues">
                        {group.issues.map((issue, index) => {
                          const severity = severityOf(issue)
                          const page = Number(issue.page)
                          return (
                            <li key={`${group.code}-${index}`} className="qa-issue">
                              <span className={`qa-issue-dot qa-issue-${severity}`} />
                              <div className="qa-issue-main">
                                <div className="qa-issue-line">
                                  {Number.isFinite(page) && page > 0 && onGotoPage ? (
                                    <button
                                      type="button"
                                      className="qa-page-chip"
                                      onClick={() => onGotoPage(page)}
                                      title={`跳到第 ${page} 页`}
                                    >
                                      P{page}
                                    </button>
                                  ) : Number.isFinite(page) && page > 0 ? (
                                    <span className="qa-page-chip qa-page-chip-static">P{page}</span>
                                  ) : null}
                                  <span className="qa-issue-message">{issue.message || QA_CODE_LABELS[group.code] || group.code}</span>
                                </div>
                                {(issue.action || issue.suggestion) && (
                <p className="qa-issue-action">{issue.action || issue.suggestion}</p>
              )}
                                <p className="qa-issue-meta">
                                  <span className={`qa-issue-severity qa-${severity === 'error' ? 'error' : severity === 'info' ? 'info' : 'warn'}`}>
                                    {severity === 'error' ? '错误' : severity === 'info' ? '提示' : '提醒'}
                                  </span>
                                  <span className="qa-issue-code">{group.code}</span>
                                </p>
                              </div>
                            </li>
                          )
                        })}
                      </ul>
                    </section>
                  ))}
                </div>
              )}

              <details className="qa-raw">
                <summary>查看原始 JSON</summary>
                <pre>{safeJson(data.raw || {}, 2)}</pre>
                <button type="button" className="btn mt-2" onClick={handleCopy}>
                  <Icon name={copied ? 'Check' : 'Copy'} className="h-3.5 w-3.5" />
                  {copied ? '已复制' : '复制原始 JSON'}
                </button>
              </details>
            </>
          )}
        </div>
      </aside>
    </>
  )
}
