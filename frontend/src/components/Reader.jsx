/* 双栏阅读器：工具栏（页码/缩放/同步/下载）、可拖分栏、状态栏（QA/下载/路径） */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { downloadUrl } from '../api.js'
import { QUALITY_MODES, STORAGE_KEYS } from '../constants.js'
import { normalizeError } from '../lib/errors.js'
import { copyText, formatDuration } from '../lib/format.js'
import { clamp, readStored, writeStored } from '../lib/storage.js'
import { useHotkeys } from '../hooks/useHotkeys.js'
import { ErrorCard } from './ErrorCard.jsx'
import { Icon } from './Icon.jsx'
import { PdfPane } from './PdfPane.jsx'
import { QaBadge } from './QaPanel.jsx'

const ZOOM_STEPS = [0.6, 0.75, 0.9, 1, 1.15, 1.3, 1.5, 1.75, 2, 2.4]

function qualityName(mode) {
  return QUALITY_MODES.find((item) => item.id === mode)?.name || ''
}

function nextZoom(current, direction) {
  const value = Number(current) || 1
  if (direction > 0) return ZOOM_STEPS.find((step) => step > value + 0.001) || ZOOM_STEPS[ZOOM_STEPS.length - 1]
  return [...ZOOM_STEPS].reverse().find((step) => step < value - 0.001) || ZOOM_STEPS[0]
}

function withBust(url, key) {
  if (!url || !key) return url
  return `${url}${url.includes('?') ? '&' : '?'}reload=${key}`
}

export function Reader({ doc, sync, zoom, onZoomChange, qa, onOpenQa, onBack, onSettings, onRepick, onNotice }) {
  const [split, setSplit] = useState(() => {
    const stored = Number(readStored(STORAGE_KEYS.split, 50))
    return clamp(Number.isFinite(stored) ? stored : 50, 28, 72)
  })
  const [fileError, setFileError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [copied, setCopied] = useState('')
  const splitRef = useRef(null)
  const dragRef = useRef(null)

  useEffect(() => {
    writeStored(STORAGE_KEYS.split, Math.round(split))
  }, [split])

  /* 缩放/分栏后保持当前页锚点 */
  useEffect(() => {
    const timer = setTimeout(() => sync.reapplyAnchor?.(), 140)
    return () => clearTimeout(timer)
  }, [zoom, split, sync.numPages, sync.reapplyAnchor])

  useEffect(() => {
    setFileError(null)
  }, [doc?.jobId])

  const handleLoadError = useCallback((side, error) => {
    const normalized = normalizeError(
      { code: 'pdf_load_failed', message: error?.message || 'PDF 载入失败', raw: String(error?.name || error?.message || error) },
      { context: 'reader' }
    )
    setFileError(normalized)
  }, [])

  const retryLoad = () => {
    setFileError(null)
    setReloadKey((key) => key + 1)
  }

  const copyPath = async () => {
    const text = doc.stats?.output_path || doc.translated || ''
    const ok = await copyText(text)
    setCopied(ok ? 'path' : '')
    onNotice?.(ok ? '已复制输出路径' : '复制失败，请手动选择文本')
    if (ok) setTimeout(() => setCopied(''), 1800)
  }

  useHotkeys({
    pageup: () => sync.gotoPage(sync.currentPage - 1),
    pagedown: () => sync.gotoPage(sync.currentPage + 1),
    home: () => sync.gotoPage(1),
    end: () => sync.gotoPage(Math.max(1, sync.numPages)),
    'mod+=': () => onZoomChange?.(nextZoom(zoom, 1)),
    'mod+-': () => onZoomChange?.(nextZoom(zoom, -1)),
    'mod+0': () => onZoomChange?.(1)
  })

  const totalPages = sync.numPages || doc.stats?.pages || 0
  const stats = doc.stats || {}
  const originalFile = withBust(doc.original, reloadKey)
  const translatedFile = withBust(doc.translated, reloadKey)
  const quality = qualityName(doc.qualityMode)
  const zoomPct = Math.round((Number(zoom) || 1) * 100)

  const startDrag = (event) => {
    const container = splitRef.current
    if (!container) return
    event.preventDefault()
    event.currentTarget.setPointerCapture?.(event.pointerId)
    dragRef.current = {
      startX: event.clientX,
      startSplit: split,
      width: container.getBoundingClientRect().width || 1
    }
  }
  const moveDrag = (event) => {
    const drag = dragRef.current
    if (!drag) return
    const delta = ((event.clientX - drag.startX) / drag.width) * 100
    setSplit(clamp(drag.startSplit + delta, 28, 72))
  }
  const endDrag = (event) => {
    dragRef.current = null
    event.currentTarget.releasePointerCapture?.(event.pointerId)
  }
  const resetSplit = () => setSplit(50)

  const statusItems = useMemo(
    () =>
      [
        `${stats.pages ?? totalPages ?? '—'} 页`,
        `回填 ${stats.translated_blocks ?? 0}/${stats.blocks ?? 0} 块`,
        `跳过 ${stats.skipped_blocks ?? 0}`,
        stats.font ? `字体 ${stats.font}` : '',
        stats.engine || '',
        stats.elapsed_ms ? formatDuration(stats.elapsed_ms) : ''
      ].filter(Boolean),
    [stats, totalPages]
  )

  return (
    <div className="reader">
      <div className="reader-toolbar reader-toolbar-main">
        <button type="button" className="icon-btn h-7 w-7" onClick={onBack} title="返回译文库" aria-label="返回译文库">
          <Icon name="ArrowLeft" className="h-3.5 w-3.5" />
        </button>
        <div className="reader-doc">
          <span className="reader-doc-name" title={doc.name}>
            {doc.name}
          </span>
          {quality ? <span className="tag tag-mint">{quality}</span> : null}
        </div>

        <div className="page-nav">
          <button
            type="button"
            className="icon-btn h-7 w-7"
            onClick={() => sync.gotoPage(sync.currentPage - 1, 0)}
            disabled={sync.currentPage <= 1}
            aria-label="上一页"
          >
            <Icon name="ChevronLeft" className="h-3.5 w-3.5" />
          </button>
          <span className="font-mono text-[10px] text-zinc-600">第</span>
          <input
            className="page-input"
            value={sync.pageDraft}
            inputMode="numeric"
            aria-label="页码"
            onChange={(event) => sync.setPageDraft(event.target.value.replace(/[^\d]/g, '').slice(0, 4))}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                sync.commitPage()
                event.currentTarget.blur()
              } else if (event.key === 'Escape') {
                sync.setPageDraft(String(sync.currentPage))
                event.currentTarget.blur()
              }
            }}
            onBlur={() => sync.commitPage()}
          />
          <span className="page-total">/ {totalPages || '—'} 页</span>
          <button
            type="button"
            className="icon-btn h-7 w-7"
            onClick={() => sync.gotoPage(sync.currentPage + 1, 0)}
            disabled={sync.currentPage >= Math.max(1, totalPages)}
            aria-label="下一页"
          >
            <Icon name="ChevronRight" className="h-3.5 w-3.5" />
          </button>
        </div>

        <span className="reader-hint hidden md:inline">PageUp / PageDown 翻页</span>

        <div className="zoom-group" role="group" aria-label="缩放">
          <button type="button" className="icon-btn h-6 w-6 border-0 bg-transparent" onClick={() => onZoomChange?.(nextZoom(zoom, -1))} aria-label="缩小">
            <Icon name="Minus" className="h-3 w-3" />
          </button>
          <span className="zoom-value">{zoomPct}%</span>
          <button type="button" className="icon-btn h-6 w-6 border-0 bg-transparent" onClick={() => onZoomChange?.(nextZoom(zoom, 1))} aria-label="放大">
            <Icon name="Plus" className="h-3 w-3" />
          </button>
          <button
            type="button"
            className={`zoom-fit ${zoomPct === 100 ? 'zoom-fit-on' : ''}`}
            onClick={() => onZoomChange?.(1)}
            title="页面宽度适配分栏"
          >
            适配宽度
          </button>
        </div>

        <button
          type="button"
          className={`sync-chip ${sync.syncEnabled ? 'sync-chip-on' : ''}`}
          onClick={sync.toggleSync}
          title={sync.syncEnabled ? '关闭同步滚动' : '开启同步滚动'}
          aria-pressed={sync.syncEnabled}
        >
          <Icon name="Sync" className="h-3.5 w-3.5" />
          {sync.syncEnabled ? '同步滚动' : '独立滚动'}
        </button>

        <a
          className="btn btn-small reader-download"
          href={downloadUrl(doc.translated)}
          download
          title="下载中文译文 PDF"
        >
          <Icon name="Download" className="h-3.5 w-3.5" />
          下载译文
        </a>
      </div>

      {fileError && (
        <div className="reader-error">
          <ErrorCard
            compact
            error={fileError}
            onRetry={retryLoad}
            onRepick={onRepick}
            onHistory={onBack}
            onSettings={onSettings}
            onClose={() => setFileError(null)}
          />
        </div>
      )}

      <div className="reader-split" ref={splitRef}>
        <div className="reader-pane reader-pane-left" style={{ width: `${split}%` }}>
          <PdfPane
            side="left"
            title="Original"
            hint="EN"
            icon="Eye"
            file={originalFile}
            zoom={zoom}
            sync={sync}
            onLoadError={handleLoadError}
          />
        </div>

        <div
          className="split-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整中英文分栏宽度"
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={resetSplit}
          title="拖动调整分栏，双击恢复 50/50"
        >
          <span />
        </div>

        <div className="reader-pane reader-pane-right">
          <PdfPane
            side="right"
            title="Translated"
            hint="ZH"
            icon="Spark"
            file={translatedFile}
            zoom={zoom}
            sync={sync}
            onLoadError={handleLoadError}
          />
        </div>
      </div>

      <div className="status-bar">
        <span className="status-done">
          <span className="status-dot" /> done
        </span>
        {statusItems.map((item) => (
          <span key={item} className="status-item">
            {item}
          </span>
        ))}
        <div className="status-actions">
          <QaBadge qa={qa} onClick={onOpenQa} />
          <a className="status-link" href={downloadUrl(doc.original)} download title="下载英文原文 PDF">
            <Icon name="Download" className="h-3.5 w-3.5" />
            原文
          </a>
          <button type="button" className="status-link" onClick={copyPath} title={stats.output_path || doc.translated}>
            <Icon name={copied ? 'Check' : 'Copy'} className="h-3.5 w-3.5" />
            {copied ? '已复制' : '复制路径'}
          </button>
        </div>
      </div>
    </div>
  )
}
