/* App 壳：外观/健康检查/历史、任务发起与轮询、阅读器与覆盖层编排 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { open as openFileDialog } from '@tauri-apps/plugin-dialog'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { api, absUrl } from './api.js'
import { APP_VERSION, MAX_UPLOAD_BYTES, MODES, QUALITY_MODES, STORAGE_KEYS, THEMES } from './constants.js'
import {
  DEMO_HISTORY,
  DEMO_PROGRESS_JOB,
  DEMO_READER_DOC,
  demoMode as demoParam,
  initialView,
  urlApiBase,
  urlAppearance
} from './demo.js'
import { normalizeError } from './lib/errors.js'
import { basename } from './lib/format.js'
import {
  API_OVERRIDE_KEY,
  getApiBase,
  isTauri,
  loadLastDoc,
  loadMode,
  loadSettings,
  loadTheme,
  saveLastDoc,
  saveSettings as persistSettings,
  setApiBase,
  writeStored
} from './lib/storage.js'
import { useHotkeys } from './hooks/useHotkeys.js'
import { useJobPolling } from './hooks/useJobPolling.js'
import { usePdfSync } from './hooks/usePdfSync.js'
import { ErrorCard } from './components/ErrorCard.jsx'
import { HelpDialog } from './components/HelpDialog.jsx'
import { HomePage } from './components/HomePage.jsx'
import { Icon } from './components/Icon.jsx'
import { LoadingOverlay } from './components/LoadingOverlay.jsx'
import { QaPanel } from './components/QaPanel.jsx'
import { Reader } from './components/Reader.jsx'
import { SettingsModal } from './components/SettingsModal.jsx'
import { Sidebar } from './components/Sidebar.jsx'

/* URL 覆盖：?apiBase= 仅本次会话（sessionStorage，不进 localStorage） */
const urlBaseOverride = urlApiBase()
if (urlBaseOverride) {
  try {
    window.sessionStorage.setItem(API_OVERRIDE_KEY, urlBaseOverride)
  } catch {
    /* ignore */
  }
}

const appearanceOverride = urlAppearance()
const DEMO_MODES = new Set(['home', 'progress', 'error', 'qa', 'reader', 'empty'])
const demoMode = DEMO_MODES.has(demoParam) ? demoParam : ''
const isDemo = Boolean(demoMode)

const DEMO_HEALTH = {
  version: APP_VERSION,
  engine: { name: 'paperex-reflow', version: APP_VERSION, revision: 'demo' },
  quality: { default_mode: 'standard', modes: QUALITY_MODES.map((item) => item.id) },
  features: {
    isolated_qa: true,
    quality_loop: true,
    job_persistence: true,
    cancel: true,
    ocr: false,
    global_cache: true,
    models_api: true
  },
  cache: { files: 128, bytes: 38_400_000, entries: 128 },
  ocr: { available: false, engine: '', hint: '演示数据：本机未安装 OCR 引擎' }
}

const DEMO_ERROR = normalizeError(
  {
    code: 'ocr_required',
    message: '这是一个扫描版 PDF：没有检测到可提取的文本层。',
    hint: '本期不支持 OCR。请换一份带文本层的 PDF，或先在本机做 OCR 后再翻译。',
    retryable: false
  },
  { context: 'job' }
)

function normalizeHistoryItem(item) {
  if (!item || typeof item !== 'object') return null
  const stats = item.stats || {}
  const qa = item.qa_summary || item.qa || null
  const error = item.error && typeof item.error === 'object' ? item.error : null
  const translatedUrl = item.translated_url || stats.translated_url || ''
  const failed = Boolean(item.failed || item.status === 'failed' || item.status === 'error' || (!translatedUrl && error))
  return {
    jobId: item.job_id || item.jobId || item.id || '',
    name: item.name || item.source_name || 'document.pdf',
    createdAt: item.created_at || item.createdAt || item.finished_at || 0,
    pages: item.pages ?? stats.pages ?? 0,
    blocks: item.blocks ?? stats.blocks ?? 0,
    elapsedMs: item.elapsed_ms ?? stats.elapsed_ms ?? 0,
    qa,
    qualityMode: item.quality_mode || stats.quality_mode || '',
    failed,
    errorMessage: error?.message || item.error_message || '',
    originalUrl: item.original_url || '',
    translatedUrl,
    raw: item
  }
}

function docFromHistory(item) {
  if (!item?.translatedUrl) return null
  return {
    jobId: item.jobId,
    name: item.name,
    original: absUrl(item.originalUrl || item.translatedUrl),
    translated: absUrl(item.translatedUrl),
    stats: {
      ...(item.raw?.stats || {}),
      pages: item.pages,
      elapsed_ms: item.elapsedMs,
      qa: item.qa || null
    },
    qualityMode: item.qualityMode,
    createdAt: item.createdAt,
    qa: item.qa || null
  }
}

function docFromJob(job, jobId, name) {
  const files = job?.files || job?.result || {}
  const translated = files.translated_url || job?.translated_url || ''
  const original = files.original_url || job?.original_url || ''
  if (!translated) return null
  const stats = { ...(job?.stats || job?.result?.stats || {}) }
  return {
    jobId: job?.job_id || jobId || '',
    name: name || job?.name || job?.file_name || 'document.pdf',
    original: absUrl(original || translated),
    translated: absUrl(translated),
    stats: {
      ...stats,
      pages: job?.pages_total ?? stats.pages,
      elapsed_ms: job?.timings_ms?.total ?? stats.elapsed_ms,
      qa: job?.qa_summary || stats.qa || null
    },
    qualityMode: job?.quality_mode || '',
    createdAt: job?.created_at || Math.floor(Date.now() / 1000),
    qa: job?.qa_summary || null
  }
}

export default function App() {
  const [theme, setTheme] = useState(() => appearanceOverride.theme || loadTheme())
  const [mode, setMode] = useState(() => appearanceOverride.mode || loadMode())
  const [settings, setSettings] = useState(() => loadSettings())
  const [backend, setBackend] = useState(() => (isDemo ? DEMO_HEALTH : null))
  const [backendOk, setBackendOk] = useState(isDemo)
  const [history, setHistory] = useState(() =>
    isDemo && demoMode !== 'empty' ? DEMO_HISTORY.map(normalizeHistoryItem).filter(Boolean) : []
  )
  const [historyState, setHistoryState] = useState(isDemo ? 'ready' : 'idle')
  const [historyError, setHistoryError] = useState(null)
  const [doc, setDoc] = useState(() => {
    if (demoMode === 'qa' || demoMode === 'reader') return DEMO_READER_DOC
    return loadLastDoc()
  })
  const [showHome, setShowHome] = useState(() => {
    if (demoMode === 'home' || demoMode === 'progress' || demoMode === 'error' || demoMode === 'empty') return true
    if (demoMode === 'qa' || demoMode === 'reader') return false
    return !loadLastDoc()
  })
  const [job, setJob] = useState(() => (demoMode === 'progress' ? DEMO_PROGRESS_JOB : null))
  const [jobId, setJobId] = useState(null)
  const [jobPhase, setJobPhase] = useState(() => (demoMode === 'progress' ? 'running' : demoMode === 'error' ? 'error' : 'idle'))
  const [jobError, setJobError] = useState(() => (demoMode === 'error' ? DEMO_ERROR : null))
  const [jobFileName, setJobFileName] = useState(() => (demoMode === 'progress' ? '长文档翻译示例：论文版式重排.pdf' : ''))
  const [jobStartedAt, setJobStartedAt] = useState(() => (demoMode === 'progress' ? Date.now() - 86_000 : 0))
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [zoom, setZoom] = useState(1)
  const [showSettings, setShowSettings] = useState(initialView === 'settings')
  const [showHelp, setShowHelp] = useState(initialView === 'help')
  const [showQa, setShowQa] = useState(initialView === 'qa' || demoMode === 'qa')
  const [qaContext, setQaContext] = useState(() => ({
    jobId: demoMode === 'qa' ? 'demo-1' : '',
    inlineQa: demoMode === 'qa' ? DEMO_READER_DOC.stats.qa : null,
    item: null
  }))

  const fileInputRef = useRef(null)
  const retryRef = useRef(null)
  const jobNameRef = useRef(jobFileName)
  const jobIdRef = useRef(null)
  const sync = usePdfSync()

  const showNotice = useCallback((text, tone = 'ok') => {
    setNotice({ text, tone, id: Date.now() })
  }, [])

  useEffect(() => {
    if (!notice) return undefined
    const timer = setTimeout(() => setNotice(null), 3400)
    return () => clearTimeout(timer)
  }, [notice])

  /* ---------- 外观 ---------- */
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.dataset.mode = mode
    if (!appearanceOverride.theme) writeStored(STORAGE_KEYS.theme, theme)
    if (!appearanceOverride.mode) writeStored(STORAGE_KEYS.mode, mode)
    if (isTauri) {
      try {
        getCurrentWindow().setTheme(mode === 'light' ? 'light' : 'dark')
      } catch {
        /* ignore */
      }
    }
  }, [theme, mode])

  const toggleMode = useCallback(() => {
    setMode((value) => (value === 'dark' ? 'light' : 'dark'))
  }, [])

  /* ---------- 后端健康 ---------- */
  useEffect(() => {
    if (isDemo) return undefined
    let alive = true
    let timer = null
    const tick = async () => {
      try {
        const data = await api.health()
        if (!alive) return
        setBackend(data && typeof data === 'object' ? data : {})
        setBackendOk(true)
        timer = setTimeout(tick, 10_000)
      } catch {
        if (!alive) return
        setBackendOk(false)
        timer = setTimeout(tick, 2500)
      }
    }
    timer = setTimeout(tick, 250)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [])

  /* ---------- 历史 ---------- */
  const refreshHistory = useCallback(async ({ quiet = false } = {}) => {
    if (isDemo) {
      setHistoryState('ready')
      return
    }
    if (!quiet) setHistoryState((state) => (state === 'ready' ? 'ready' : 'loading'))
    try {
      const data = await api.listHistory()
      const items = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : []
      setHistory(items.map(normalizeHistoryItem).filter(Boolean))
      setHistoryState('ready')
      setHistoryError(null)
      setBackendOk(true)
    } catch (caught) {
      setHistoryError(normalizeError(caught, { context: 'history' }))
      setHistoryState('error')
    }
  }, [])

  useEffect(() => {
    if (isDemo) return
    if (!backendOk) return
    refreshHistory({ quiet: historyState === 'ready' })
    // 只在后端从离线恢复在线时刷新一次；其余刷新由操作显式触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendOk])

  /* ---------- 任务轮询 ---------- */
  const finishWithJob = useCallback(
    (finished) => {
      const nextDoc = docFromJob(finished, jobIdRef.current, jobNameRef.current)
      setJob(null)
      setJobPhase('idle')
      setJobId(null)
      jobIdRef.current = null
      retryRef.current = null
      if (nextDoc) {
        setDoc(nextDoc)
        saveLastDoc(nextDoc)
        setShowHome(false)
        setZoom(1)
        setError(null)
        showNotice('翻译完成，已打开双语对照')
      } else {
        showNotice('任务完成，但没有拿到译文文件地址', 'warn')
      }
      refreshHistory({ quiet: true })
    },
    [refreshHistory, showNotice]
  )

  const failWithJob = useCallback((normalized) => {
    setJobPhase('error')
    setJobError(normalized)
    setJob(null)
    setJobId(null)
    jobIdRef.current = null
  }, [])

  const jobPolling = useJobPolling(jobId, {
    onUpdate: (next) => {
      setJob(next)
      setJobPhase('running')
    },
    onDone: finishWithJob,
    onError: failWithJob,
    onCanceled: () => {
      setJob(null)
      setJobPhase('idle')
      setJobId(null)
      jobIdRef.current = null
      retryRef.current = null
      showNotice('任务已取消，没有生成半成品文件')
      refreshHistory({ quiet: true })
    }
  })

  /* ---------- 发起任务 ---------- */
  const startJob = useCallback(
    async (source) => {
      if (!source) return
      setError(null)
      setJobError(null)
      setJob(null)
      setJobPhase('starting')
      setJobStartedAt(Date.now())
      setJobFileName(source.name || 'document.pdf')
      jobNameRef.current = source.name || 'document.pdf'
      retryRef.current = source

      try {
        let pdfPath = source.path
        if (source.kind === 'file') {
          const uploaded = await api.uploadFile(source.file)
          pdfPath = uploaded?.pdf_path || uploaded?.path || ''
        }
        if (!pdfPath) throw new Error('上传完成但没有拿到文件路径')
        const response = await api.createJob({
          pdf_path: pdfPath,
          api_key: settings.apiKey ? settings.apiKey : undefined,
          base_url: settings.baseUrl,
          model: settings.model,
          target_lang: settings.targetLang || 'zh',
          quality_mode: settings.qualityMode,
          thinking: Boolean(settings.thinking),
          reasoning_effort: settings.reasoningEffort,
          use_cache: Boolean(settings.useCache)
        })
        const nextJobId = response?.job_id || response?.jobId || ''
        if (!nextJobId) throw new Error('后端没有返回 job_id')
        jobIdRef.current = nextJobId
        setJobId(nextJobId)
        setJob({ status: 'queued', stage: 'queued', stage_label: '排队中', progress: 0 })
        setJobPhase('running')
        setBackendOk(true)
      } catch (caught) {
        failWithJob(normalizeError(caught, { context: 'upload' }))
      }
    },
    [failWithJob, settings]
  )

  const pickFile = useCallback(async () => {
    setError(null)
    if (jobPhase === 'running' || jobPhase === 'starting') {
      showNotice('已有任务在进行中，先取消或等它完成', 'warn')
      return
    }
    if (isTauri) {
      try {
        const selected = await openFileDialog({
          multiple: false,
          directory: false,
          title: '选择一篇英文 PDF 论文',
          filters: [{ name: 'PDF 文档', extensions: ['pdf'] }]
        })
        if (!selected) return
        const path = typeof selected === 'string' ? selected : selected.path
        if (!path) return
        await startJob({ kind: 'path', path, name: basename(path) })
      } catch (caught) {
        setError(normalizeError({ message: `无法打开文件选择器：${caught?.message || caught}` }, { context: 'upload' }))
      }
      return
    }
    fileInputRef.current?.click()
  }, [jobPhase, showNotice, startJob])

  const acceptFile = useCallback(
    (file) => {
      if (!file) return
      const name = file.name || 'document.pdf'
      if (!/\.pdf$/i.test(name)) {
        setError(normalizeError({ code: 'invalid_file_type', message: `只支持 PDF 文件，当前选择的是 ${name}` }, { context: 'upload' }))
        return
      }
      if (file.size && file.size > MAX_UPLOAD_BYTES) {
        setError(
          normalizeError(
            { code: 'file_too_large', message: `文件 ${(file.size / 1024 / 1024).toFixed(1)}MB，超过 120MB 上限` },
            { context: 'upload' }
          )
        )
        return
      }
      startJob({ kind: 'file', file, name })
    },
    [startJob]
  )

  const onBrowserFile = useCallback(
    (event) => {
      const file = event.target.files?.[0]
      event.target.value = ''
      acceptFile(file)
    },
    [acceptFile]
  )

  const retryJob = useCallback(() => {
    const source = retryRef.current
    if (!source) {
      showNotice('没有可重试的任务，请重新选择文件', 'warn')
      return
    }
    startJob(source)
  }, [showNotice, startJob])

  const cancelJob = useCallback(async () => {
    /* 演示/尚未拿到 job_id（正在上传）时：本地直接收起遮罩 */
    if (!jobId) {
      setJobPhase('idle')
      setJob(null)
      setShowHome(true)
      showNotice(isDemo ? '已取消（演示任务不会真正启动）' : '已取消', 'warn')
      return
    }
    try {
      await jobPolling.cancel()
      showNotice('已请求取消，正在停止任务…')
    } catch (caught) {
      setError(normalizeError(caught, { context: 'cancel' }))
    }
  }, [isDemo, jobId, jobPolling, showNotice])

  /* ---------- 打开/关闭译文 ---------- */
  const openDoc = useCallback((nextDoc) => {
    if (!nextDoc) return
    setError(null)
    setZoom(1)
    setDoc(nextDoc)
    saveLastDoc(nextDoc)
    setShowHome(false)
    requestAnimationFrame(() => sync.gotoPage(1))
  }, [sync])

  const openHistoryItem = useCallback(
    (item) => {
      if (!item) return
      const nextDoc = docFromHistory(item)
      if (!nextDoc) {
        setError(
          normalizeError(
            item.raw?.error || { code: 'no_translation', message: item.errorMessage || '这条记录没有可用的译文文件' },
            { context: 'history' }
          )
        )
        return
      }
      openDoc(nextDoc)
    },
    [openDoc]
  )

  const goHome = useCallback(() => {
    setShowHome(true)
    setError(null)
  }, [])

  const clearDoc = useCallback(() => {
    setDoc(null)
    saveLastDoc(null)
    setShowHome(true)
  }, [])

  const openQa = useCallback((context) => {
    setQaContext({ jobId: context?.jobId || '', inlineQa: context?.inlineQa || null, item: context?.item || null })
    setShowQa(true)
  }, [])

  const handleQaGoto = useCallback(
    (page) => {
      setShowQa(false)
      if (!doc && qaContext.item) {
        openHistoryItem(qaContext.item)
        setTimeout(() => sync.gotoPage(page), 420)
      } else {
        sync.gotoPage(page)
      }
    },
    [doc, openHistoryItem, qaContext.item, sync]
  )

  /* ---------- 删除历史 ---------- */
  const removeHistory = useCallback(
    async (jobIds) => {
      const ids = Array.isArray(jobIds) ? jobIds : [jobIds]
      let removed = new Set()
      if (isDemo) {
        removed = new Set(ids)
      } else {
        const results = await Promise.allSettled(ids.map((id) => api.deleteHistory(id)))
        removed = new Set(ids.filter((id, index) => results[index].status === 'fulfilled'))
        const failed = results.filter((result) => result.status === 'rejected')
        if (failed.length) {
          setError(normalizeError(failed[0].reason, { context: 'delete' }))
        }
      }
      if (removed.size) {
        setHistory((list) => list.filter((item) => !removed.has(item.jobId)))
        if (doc?.jobId && removed.has(doc.jobId)) {
          clearDoc()
          showNotice('当前正在阅读的译文已删除，已返回译文库')
        } else {
          showNotice(removed.size === 1 ? '已删除这条记录' : `已删除 ${removed.size} 条记录`)
        }
      }
    },
    [clearDoc, doc?.jobId, isDemo, showNotice]
  )

  /* ---------- 设置 ---------- */
  const handleAppearance = useCallback(({ theme: nextTheme, mode: nextMode }) => {
    if (THEMES.some((item) => item.id === nextTheme)) setTheme(nextTheme)
    if (MODES.some((item) => item.id === nextMode)) setMode(nextMode)
  }, [])

  const handleSaveSettings = useCallback(
    async (draft, apiBaseValue) => {
      const previousBase = getApiBase()
      persistSettings(draft)
      setSettings(draft)
      if (apiBaseValue) setApiBase(apiBaseValue)
      setShowSettings(false)
      if (apiBaseValue && apiBaseValue !== previousBase) {
        showNotice('后端地址已保存，重启应用后生效')
      } else {
        showNotice('设置已保存，下次翻译生效')
      }
      if (!isDemo) {
        try {
          const health = await api.health()
          setBackend(health && typeof health === 'object' ? health : {})
          setBackendOk(true)
        } catch {
          setBackendOk(false)
        }
      }
    },
    [showNotice]
  )

  /* ---------- 快捷键 ---------- */
  useHotkeys({
    'mod+o': () => pickFile(),
    'mod+,': () => setShowSettings(true),
    '?': () => setShowHelp(true),
    escape: () => {
      if (showQa) setShowQa(false)
      else if (showHelp) setShowHelp(false)
      else if (showSettings) setShowSettings(false)
      else if (error) setError(null)
    }
  })

  /* ---------- 派生 ---------- */
  const busy = jobPhase === 'running' || jobPhase === 'starting'
  const historyCount = history.length
  const recent = useMemo(
    () => history.slice(0, 8).map((item) => ({ jobId: item.jobId, name: item.name, createdAt: item.createdAt, failed: item.failed, qa: item.qa })),
    [history]
  )
  const readerQa = doc?.qa || doc?.stats?.qa || null
  const demoModeLabel = demoMode ? `演示：${demoMode}` : ''

  return (
    <div className="app-shell">
      <div className="app-ambient" aria-hidden="true" />

      <Sidebar
        mode={mode}
        theme={theme}
        historyCount={historyCount}
        recent={recent}
        currentJobId={doc?.jobId}
        backendOnline={backendOk}
        busy={busy}
        onNew={pickFile}
        onHome={goHome}
        onSettings={() => setShowSettings(true)}
        onHelp={() => setShowHelp(true)}
        onToggleMode={toggleMode}
        onOpenRecent={openHistoryItem}
      />

      <div className="app-main">
        <header className="app-header">
          <div className="app-crumbs">
            <span className="app-crumb-brand">paperex</span>
            <span className="app-crumb-sep">/</span>
            <span className="app-crumb-current" title={showHome || !doc ? '译文库' : doc.name}>
              {showHome || !doc ? '译文库' : doc.name}
            </span>
            {!backendOk && !isDemo ? <span className="app-offline">后端离线</span> : null}
            {demoModeLabel ? <span className="app-demo-tag">{demoModeLabel}</span> : null}
          </div>
          <div className="app-header-actions">
            <span className="app-version">v{APP_VERSION}</span>
            <button type="button" className="icon-btn h-8 w-8" onClick={() => setShowHelp(true)} title="快捷键（?）" aria-label="快捷键帮助">
              <Icon name="Help" className="h-4 w-4" />
            </button>
            <button type="button" className="icon-btn h-8 w-8" onClick={() => setShowSettings(true)} title="设置（Ctrl+,）" aria-label="打开设置">
              <Icon name="Gear" className="h-4 w-4" />
            </button>
          </div>
        </header>

        <main className="app-content">
          {showHome || !doc ? (
            <HomePage
              items={history}
              state={historyState}
              error={historyError}
              backendOnline={backendOk}
              backend={backend}
              demo={isDemo}
              busy={busy}
              onReload={() => refreshHistory()}
              onOpen={openHistoryItem}
              onOpenQa={(item) => openQa({ jobId: item.jobId, inlineQa: item.qa, item })}
              onDelete={(jobId) => removeHistory([jobId])}
              onDeleteMany={(ids) => removeHistory(ids)}
              onNew={pickFile}
              onSettings={() => setShowSettings(true)}
              onHelp={() => setShowHelp(true)}
              onDropFile={acceptFile}
            />
          ) : (
            <Reader
              doc={doc}
              sync={sync}
              zoom={zoom}
              onZoomChange={setZoom}
              qa={readerQa}
              onOpenQa={() => openQa({ jobId: doc.jobId, inlineQa: readerQa, item: null })}
              onBack={goHome}
              onSettings={() => setShowSettings(true)}
              onRepick={pickFile}
              onNotice={showNotice}
            />
          )}
        </main>
      </div>

      <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" className="hidden-input" onChange={onBrowserFile} />

      {busy && (
        <LoadingOverlay
          job={job}
          fileName={jobFileName}
          startedAt={jobStartedAt}
          onCancel={cancelJob}
          cancelState={jobPolling.cancelState}
          demo={Boolean(demoMode)}
        />
      )}

      {jobPhase === 'error' && jobError && (
        <div className="job-error-overlay">
          <div className="job-error-card">
            <ErrorCard
              error={jobError}
              onClose={() => {
                setJobError(null)
                setJobPhase('idle')
              }}
              onRetry={retryRef.current ? retryJob : null}
              onSettings={() => setShowSettings(true)}
              onHistory={goHome}
              onRepick={pickFile}
            />
          </div>
        </div>
      )}

      {showSettings && (
        <SettingsModal
          open={showSettings}
          settings={settings}
          health={backend}
          onClose={() => setShowSettings(false)}
          onSave={handleSaveSettings}
          onAppearance={handleAppearance}
          theme={theme}
          mode={mode}
          onNotice={(text) => showNotice(text)}
        />
      )}

      <HelpDialog open={showHelp} onClose={() => setShowHelp(false)} />

      <QaPanel
        open={showQa}
        onClose={() => setShowQa(false)}
        jobId={qaContext.jobId}
        inlineQa={qaContext.inlineQa}
        onGotoPage={handleQaGoto}
      />

      {error && (
        <div className="toast-wrap">
          <ErrorCard
            compact
            error={error}
            onClose={() => setError(null)}
            onRetry={error.retryable && retryRef.current ? retryJob : null}
            onSettings={() => setShowSettings(true)}
            onHistory={goHome}
            onRepick={pickFile}
          />
        </div>
      )}

      {notice && (
        <div className={`notice-toast notice-${notice.tone || 'ok'}`} role="status" aria-live="polite">
          <Icon name={notice.tone === 'warn' ? 'AlertTriangle' : 'Check'} className="h-3.5 w-3.5" />
          {notice.text}
        </div>
      )}
    </div>
  )
}
