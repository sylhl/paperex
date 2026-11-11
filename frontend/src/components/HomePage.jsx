/* 译文库首页：搜索、QA 徽标、失败原因、多选删除、加载/错误/空态、拖拽上传 */

import { useMemo, useRef, useState } from 'react'
import { QUALITY_MODES, MAX_UPLOAD_BYTES } from '../constants.js'
import { formatBytes, formatDate, formatDuration, qaCounts } from '../lib/format.js'
import { ErrorCard } from './ErrorCard.jsx'
import { Icon } from './Icon.jsx'
import { QaBadge } from './QaPanel.jsx'

function qualityName(mode) {
  return QUALITY_MODES.find((item) => item.id === mode)?.name || ''
}

function HistorySkeleton() {
  return (
    <ul className="history-list" aria-hidden="true">
      {[0, 1, 2, 3].map((index) => (
        <li key={index} className="history-row history-row-skeleton">
          <span className="history-skel-icon" />
          <span className="history-skel-main">
            <span className="history-skel-line history-skel-line-wide" />
            <span className="history-skel-line" />
          </span>
        </li>
      ))}
    </ul>
  )
}

export function HomePage({
  items,
  state,
  error,
  backendOnline,
  backend,
  demo,
  busy,
  onReload,
  onOpen,
  onOpenQa,
  onDelete,
  onDeleteMany,
  onNew,
  onSettings,
  onHelp,
  onDropFile
}) {
  const [query, setQuery] = useState('')
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [confirm, setConfirm] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const dragDepth = useRef(0)

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return items
    return items.filter((item) => String(item.name || '').toLowerCase().includes(needle))
  }, [items, query])

  const toggleSelect = (jobId) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  const exitSelecting = () => {
    setSelecting(false)
    setSelected(new Set())
  }

  const askDelete = (item) => setConfirm({ ids: [item.jobId], name: item.name })
  const askDeleteSelected = () => {
    if (selected.size === 0) return
    const names = items.filter((item) => selected.has(item.jobId)).map((item) => item.name)
    setConfirm({ ids: Array.from(selected), name: names.length === 1 ? names[0] : `${names.length} 条记录` })
  }

  const runDelete = async () => {
    if (!confirm) return
    setDeleting(true)
    try {
      if (confirm.ids.length === 1) await onDelete(confirm.ids[0])
      else await onDeleteMany(confirm.ids)
      setConfirm(null)
      exitSelecting()
    } finally {
      setDeleting(false)
    }
  }

  const handleDragEnter = (event) => {
    if (!onDropFile || !event.dataTransfer?.types?.includes('Files')) return
    event.preventDefault()
    dragDepth.current += 1
    setDragging(true)
  }
  const handleDragOver = (event) => {
    if (!onDropFile) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }
  const handleDragLeave = () => {
    if (!onDropFile) return
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragging(false)
  }
  const handleDrop = (event) => {
    if (!onDropFile) return
    event.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    const file = event.dataTransfer?.files?.[0]
    if (file) onDropFile(file)
  }

  const backendVersion = backend?.version ? `后端 v${backend.version}` : ''
  const cacheText = backend?.cache?.files ? `缓存 ${backend.cache.files} 项 · ${formatBytes(backend.cache.bytes)}` : ''

  return (
    <div
      className="home"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragging && (
        <div className="drop-overlay">
          <div className="drop-card">
            <Icon name="File" className="h-8 w-8" />
            <p>松开鼠标，开始翻译这份 PDF</p>
            <span>支持 .pdf，最大 120MB</span>
          </div>
        </div>
      )}

      <header className="home-hero">
        <div className="home-hero-main">
          <p className="home-eyebrow">
            <span className="home-eyebrow-dot" /> PAPEREX v1.1 · 论文 PDF 翻译与版式回填
          </p>
          <h1 className="home-title">把英文论文变成能直接读的中文版</h1>
          <p className="home-sub">
            保留公式、图表和双栏版式，译后质检自动检查溢出与缺译。选择一份 PDF，剩下的交给 paperex。
          </p>
          <div className="home-actions">
            <button type="button" className="btn btn-primary home-cta" onClick={onNew} disabled={busy}>
              <Icon name="Plus" className="h-4 w-4" />
              选择 PDF 开始翻译
            </button>
            <button type="button" className="btn btn-ghost" onClick={onHelp}>
              <Icon name="Help" className="h-4 w-4" />
              快捷键
            </button>
          </div>
          <div className="home-meta">
            <span className={`home-pill ${backendOnline ? 'home-pill-on' : 'home-pill-off'}`}>
              <span className={`side-status-dot ${backendOnline ? 'side-status-on' : 'side-status-off'}`} />
              {backendOnline ? backendVersion || '后端在线' : '后端离线'}
            </span>
            {backend?.quality?.default_mode && (
              <span className="home-pill">默认质量 {qualityName(backend.quality.default_mode)}</span>
            )}
            {backend?.ocr && !backend.ocr.available && <span className="home-pill home-pill-warn">未检测到 OCR 引擎</span>}
            {cacheText && <span className="home-pill">{cacheText}</span>}
            {demo && <span className="home-pill home-pill-demo">演示数据</span>}
          </div>
        </div>

        <aside className="home-guide">
          <p className="home-guide-title">三步读完一篇论文</p>
          <ol className="home-guide-list">
            <li>
              <span className="home-guide-num">1</span>
              <div>
                <strong>选择 PDF</strong>
                <span>拖到窗口里，或点击按钮选择</span>
              </div>
            </li>
            <li>
              <span className="home-guide-num">2</span>
              <div>
                <strong>等它翻译</strong>
                <span>进度里能看到阶段、页码与剩余时间</span>
              </div>
            </li>
            <li>
              <span className="home-guide-num">3</span>
              <div>
                <strong>双栏对照阅读</strong>
                <span>左侧原文右侧译文，QA 报告可跳页</span>
              </div>
            </li>
          </ol>
          {!backendOnline && (
            <p className="home-guide-warn">
              本地后端没有启动。启动命令见错误提示，或先看快捷键帮助。
            </p>
          )}
        </aside>
      </header>

      <section className="home-history" aria-label="译文库">
        <div className="home-history-head">
          <div>
            <h2 className="home-history-title">译文库</h2>
            <p className="home-history-sub">
              {state === 'ready' ? `${items.length} 条翻译记录` : '正在读取记录…'}
              {state === 'ready' && items.length > 0 ? ' · 点击恢复阅读或查看 QA' : ''}
            </p>
          </div>
          <div className="home-tools">
            <label className="home-search">
              <Icon name="Search" className="h-3.5 w-3.5" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索文件名…"
                aria-label="搜索译文库"
              />
              {query && (
                <button type="button" className="icon-btn h-6 w-6" onClick={() => setQuery('')} aria-label="清除搜索">
                  <Icon name="Close" className="h-3 w-3" />
                </button>
              )}
            </label>
            <button type="button" className="icon-btn" onClick={onReload} title="刷新译文库" aria-label="刷新译文库">
              <Icon name="Refresh" className="h-4 w-4" />
            </button>
            <button
              type="button"
              className={`btn btn-small ${selecting ? 'btn-active' : ''}`}
              onClick={() => (selecting ? exitSelecting() : setSelecting(true))}
              disabled={items.length === 0}
            >
              <Icon name="List" className="h-3.5 w-3.5" />
              {selecting ? '退出多选' : '多选'}
            </button>
            <button type="button" className="btn btn-small" onClick={onSettings}>
              <Icon name="Gear" className="h-3.5 w-3.5" />
              设置
            </button>
          </div>
        </div>

        {state === 'loading' && <HistorySkeleton />}

        {state === 'error' && (
          <div className="home-error">
            <ErrorCard
              compact
              error={error}
              onRetry={onReload}
              onSettings={onSettings}
              onClose={null}
            />
          </div>
        )}

        {state === 'ready' && items.length === 0 && (
          <div className="home-empty">
            <span className="home-empty-icon">
              <Icon name="File" className="h-7 w-7" />
            </span>
            <h3>译文库还是空的</h3>
            <p>选一份英文论文试试。翻译完成后，原文和译文会一起保存在这里，随时可以回来对照阅读。</p>
            <button type="button" className="btn btn-primary" onClick={onNew} disabled={busy}>
              <Icon name="Plus" className="h-4 w-4" />
              选择 PDF 开始翻译
            </button>
            <p className="home-empty-hint">支持 .pdf，最大 {formatBytes(MAX_UPLOAD_BYTES)}；扫描版 PDF 需要 OCR（本期暂不支持）。</p>
          </div>
        )}

        {state === 'ready' && items.length > 0 && filtered.length === 0 && (
          <div className="home-empty home-empty-small">
            <h3>没有匹配「{query}」的记录</h3>
            <button type="button" className="btn" onClick={() => setQuery('')}>
              清除搜索词
            </button>
          </div>
        )}

        {state === 'ready' && filtered.length > 0 && (
          <ul className="history-list">
            {filtered.map((item) => {
              const counts = qaCounts(item.qa)
              const meta = [
                formatDate(item.createdAt),
                item.pages ? `${item.pages} 页` : '',
                item.elapsedMs ? formatDuration(item.elapsedMs) : '',
                qualityName(item.qualityMode) ? `质量 ${qualityName(item.qualityMode)}` : ''
              ].filter(Boolean)
              return (
                <li key={item.jobId} className={`history-row ${item.failed ? 'history-row-failed' : ''}`}>
                  {selecting && (
                    <label className="history-check">
                      <input type="checkbox" checked={selected.has(item.jobId)} onChange={() => toggleSelect(item.jobId)} aria-label={`选择 ${item.name}`} />
                      <span />
                    </label>
                  )}
                  <button
                    type="button"
                    className="history-open"
                    onClick={() => onOpen(item)}
                    title={item.failed ? item.errorMessage || '这条记录没有可用译文' : `打开 ${item.name}`}
                  >
                    <span className={`history-icon ${item.failed ? 'history-icon-failed' : ''}`}>
                      <Icon name={item.failed ? 'AlertCircle' : 'File'} className="h-4 w-4" />
                    </span>
                    <span className="history-main">
                      <span className="history-name">{item.name}</span>
                      <span className="history-meta">
                        {meta.map((part, index) => (
                          <span key={part}>
                            {index > 0 && <span className="history-meta-dot">·</span>}
                            {part}
                          </span>
                        ))}
                        {item.failed && <span className="history-fail-reason">失败：{item.errorMessage || '没有可用译文'}</span>}
                      </span>
                    </span>
                  </button>
                  <div className="history-side">
                    {!item.failed && (
                      <QaBadge qa={item.qa} onClick={() => onOpenQa(item)} />
                    )}
                    {item.failed && <span className="history-fail-tag">无译文</span>}
                    <button
                      type="button"
                      className="icon-btn history-delete"
                      onClick={() => askDelete(item)}
                      title="删除记录"
                      aria-label={`删除 ${item.name}`}
                    >
                      <Icon name="Trash" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}

        {selecting && (
          <div className="history-bulkbar">
            <span>已选 {selected.size} 条</span>
            <div className="flex gap-2">
              <button type="button" className="btn btn-small" onClick={exitSelecting}>
                取消
              </button>
              <button type="button" className="btn btn-small btn-danger" onClick={askDeleteSelected} disabled={selected.size === 0}>
                <Icon name="Trash" className="h-3.5 w-3.5" />
                删除选中
              </button>
            </div>
          </div>
        )}
      </section>

      {confirm && (
        <div className="confirm-overlay" role="alertdialog" aria-modal="true" aria-label="确认删除">
          <div className="confirm-card">
            <h3>删除「{confirm.name}」？</h3>
            <p>删除后原文、译文和 QA 报告都会被移除，无法恢复。</p>
            <div className="confirm-actions">
              <button type="button" className="btn" onClick={() => setConfirm(null)} disabled={deleting}>
                取消
              </button>
              <button type="button" className="btn btn-danger" onClick={runDelete} disabled={deleting}>
                {deleting ? '正在删除…' : '删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
