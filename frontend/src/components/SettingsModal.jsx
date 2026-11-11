/* 设置弹窗：外观、翻译服务（模型目录/测试连接）、质量模式、高级信息 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import {
  APP_VERSION,
  DEFAULT_API_BASE,
  DEFAULT_SETTINGS,
  MODEL_FALLBACKS,
  MODES,
  PROVIDER_PRESETS,
  QUALITY_MODES,
  REASONING_EFFORTS,
  THEMES
} from '../constants.js'
import { normalizeError } from '../lib/errors.js'
import { formatBytes } from '../lib/format.js'
import { getApiBase } from '../lib/storage.js'
import { Icon } from './Icon.jsx'

const EMPTY_HEALTH = {}

function validate(draft, apiBase) {
  const errors = {}
  const base = String(draft.baseUrl || '').trim()
  if (!base) errors.baseUrl = '请填写服务地址（DeepSeek 通常以 /v1 结尾）'
  else if (!/^https?:\/\//i.test(base)) errors.baseUrl = '地址需要以 http:// 或 https:// 开头'
  if (!String(draft.model || '').trim()) errors.model = '请填写模型名，或从推荐列表中选择'
  const backend = String(apiBase || '').trim()
  if (!backend) errors.apiBase = '请填写本地后端地址'
  else if (!/^https?:\/\//i.test(backend)) errors.apiBase = '本地后端地址需要以 http:// 或 https:// 开头'
  return errors
}

function detectProvider(baseUrl) {
  const value = String(baseUrl || '')
  const preset = PROVIDER_PRESETS.find((item) => item.id !== 'custom' && item.baseUrl && value.startsWith(item.baseUrl.replace(/\/v1\/?$/, '')))
  return preset ? preset.id : 'custom'
}

export function SettingsModal({ open, settings, health, onClose, onSave, onAppearance, theme, mode, onNotice }) {
  const [draft, setDraft] = useState(settings)
  const [apiBase, setApiBase] = useState(() => getApiBase())
  const [providerId, setProviderId] = useState(() => detectProvider(settings.baseUrl))
  const [showKey, setShowKey] = useState(false)
  const [touched, setTouched] = useState({})
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [models, setModels] = useState([])
  const [modelsNote, setModelsNote] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setDraft(settings)
    setApiBase(getApiBase())
    setProviderId(detectProvider(settings.baseUrl))
    setTestResult(null)
    setTouched({})
    let alive = true
    api
      .getModels()
      .then((data) => {
        if (!alive) return
        const recommended = Array.isArray(data?.recommended) ? data.recommended : []
        const fromProviders = []
        if (Array.isArray(data?.providers)) {
          data.providers.forEach((provider) => {
            if (Array.isArray(provider?.models)) {
              provider.models.forEach((model) => {
                const value = typeof model === 'string' ? model : model?.model || model?.id
                if (value) fromProviders.push({ model: value, label: typeof model === 'string' ? model : model.label || value })
              })
            }
          })
        }
        const list = [...recommended, ...fromProviders, ...MODEL_FALLBACKS]
        const seen = new Set()
        setModels(list.filter((item) => item && item.model && !seen.has(item.model) && seen.add(item.model)))
        setModelsNote('')
      })
      .catch((error) => {
        if (!alive) return
        setModels(MODEL_FALLBACKS)
        setModelsNote(normalizeError(error, { context: 'settings' }).title)
      })
    return () => {
      alive = false
    }
  }, [open, settings])

  const errors = useMemo(() => validate(draft, apiBase), [draft, apiBase])
  const hasErrors = Object.keys(errors).length > 0
  const thinkingAvailable = providerId === 'deepseek' || /deepseek/i.test(draft.baseUrl || '')
  const engine = health?.engine || EMPTY_HEALTH
  const cache = health?.cache || EMPTY_HEALTH
  const features = health?.features || {}

  if (!open) return null

  const patch = (values) => setDraft((prev) => ({ ...prev, ...values }))
  const markTouched = (field) => setTouched((prev) => ({ ...prev, [field]: true }))

  const chooseProvider = (preset) => {
    setProviderId(preset.id)
    if (preset.id !== 'custom') {
      patch({ baseUrl: preset.baseUrl, model: preset.model })
    }
  }

  const runTest = async () => {
    if (hasErrors) {
      setTouched({ baseUrl: true, model: true })
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.testConnection({
        base_url: String(draft.baseUrl).trim(),
        model: String(draft.model).trim(),
        api_key: draft.apiKey ? String(draft.apiKey).trim() : undefined
      })
      if (result && result.ok === false) {
        setTestResult({ ok: false, message: result.message || '连接失败', latency: result.latency_ms })
      } else {
        setTestResult({
          ok: true,
          message: result?.message || '连接成功',
          latency: Number(result?.latency_ms) || 0,
          models: result?.models
        })
      }
    } catch (error) {
      const normalized = normalizeError(error, { context: 'settings' })
      setTestResult({ ok: false, message: `${normalized.title}：${normalized.detail}`, hint: normalized.hint })
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    if (hasErrors) {
      setTouched({ baseUrl: true, model: true, apiBase: true })
      return
    }
    setSaving(true)
    try {
      await onSave(
        {
          ...draft,
          baseUrl: String(draft.baseUrl).trim(),
          model: String(draft.model).trim(),
          apiKey: String(draft.apiKey || '').trim()
        },
        String(apiBase).trim()
      )
      onNotice?.('设置已保存')
    } finally {
      setSaving(false)
    }
  }

  const resetDefaults = () => {
    setDraft({ ...DEFAULT_SETTINGS })
    setApiBase(DEFAULT_API_BASE)
    setProviderId('deepseek')
    setTestResult(null)
    onNotice?.('已恢复默认值，点保存后生效')
  }

  return (
    <div className="settings-overlay" role="dialog" aria-modal="true" aria-label="设置">
      <div className="settings-shell">
        <span className="settings-aurora" aria-hidden="true" />
        <span className="settings-mesh" aria-hidden="true" />

        <header className="settings-head">
          <div className="flex items-center gap-3">
            <span className="settings-mark">P</span>
            <div>
              <p className="settings-eyebrow">PAPEREX · SETTINGS</p>
              <h2 className="settings-title">设置</h2>
              <p className="settings-sub">外观即时生效；翻译服务与质量在保存后用于下一次翻译。</p>
            </div>
          </div>
          <button type="button" className="settings-close" onClick={onClose} aria-label="关闭设置">
            <Icon name="Close" className="h-4 w-4" />
          </button>
        </header>

        <div className="settings-body">
          <section className="settings-card">
            <div className="settings-card-head">
              <span className="settings-icon">
                <Icon name={mode === 'dark' ? 'Moon' : 'Sun'} className="h-4 w-4" />
              </span>
              <div>
                <h3 className="settings-card-title">外观</h3>
                <p className="settings-card-sub">深浅模式与主题色，切换后立即生效。</p>
              </div>
              <span className="settings-pill">{MODES.find((item) => item.id === mode)?.name || '深色'}</span>
            </div>
            <div className="settings-card-body">
              <div className="settings-field">
                <span className="settings-label">显示模式</span>
                <div className="mode-switch" role="group" aria-label="显示模式">
                  {MODES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`mode-option ${mode === item.id ? 'mode-option-active' : ''}`}
                      onClick={() => onAppearance?.({ theme, mode: item.id })}
                    >
                      <Icon name={item.id === 'dark' ? 'Moon' : 'Sun'} className="h-3.5 w-3.5" />
                      {item.name}
                    </button>
                  ))}
                </div>
              </div>
              <div className="settings-field">
                <span className="settings-label">主题色</span>
                <div className="theme-grid">
                  {THEMES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`theme-option ${theme === item.id ? 'theme-option-active' : ''}`}
                      onClick={() => onAppearance?.({ theme: item.id, mode })}
                    >
                      <span className="theme-swatch" style={{ background: item.swatch }} />
                      <span className="theme-name">{item.name}</span>
                      {theme === item.id && <Icon name="Check" className="theme-tick h-3 w-3" />}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <span className="settings-icon">
                <Icon name="Key" className="h-4 w-4" />
              </span>
              <div>
                <h3 className="settings-card-title">翻译服务</h3>
                <p className="settings-card-sub">OpenAI 兼容接口；API Key 只保存在本机。</p>
              </div>
              <span className={`settings-pill ${testResult?.ok ? 'settings-pill-ok' : ''}`}>
                {testResult?.ok ? '已连通' : '未测试'}
              </span>
            </div>
            <div className="settings-card-body">
              <div className="preset-row" role="group" aria-label="服务商预设">
                {PROVIDER_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className={`preset-chip ${providerId === preset.id ? 'preset-chip-active' : ''}`}
                    onClick={() => chooseProvider(preset)}
                  >
                    {preset.name}
                  </button>
                ))}
              </div>
              <p className="preset-note">{PROVIDER_PRESETS.find((item) => item.id === providerId)?.note}</p>

              <div className="settings-field">
                <label className="settings-label" htmlFor="settings-base-url">
                  服务地址 base_url
                </label>
                <input
                  id="settings-base-url"
                  className={`field ${touched.baseUrl && errors.baseUrl ? 'field-invalid' : ''}`}
                  value={draft.baseUrl}
                  onChange={(event) => patch({ baseUrl: event.target.value })}
                  onBlur={() => markTouched('baseUrl')}
                  placeholder="https://api.deepseek.com/v1"
                  spellCheck="false"
                />
                {touched.baseUrl && errors.baseUrl && <p className="field-error">{errors.baseUrl}</p>}
              </div>

              <div className="settings-field">
                <label className="settings-label" htmlFor="settings-model">
                  模型
                  <span className="settings-optional">目录来自后端 /api/models，也可手动填写</span>
                </label>
                <input
                  id="settings-model"
                  className={`field ${touched.model && errors.model ? 'field-invalid' : ''}`}
                  value={draft.model}
                  onChange={(event) => patch({ model: event.target.value })}
                  onBlur={() => markTouched('model')}
                  list="settings-model-options"
                  placeholder="deepseek-chat"
                  spellCheck="false"
                />
                <datalist id="settings-model-options">
                  {models.map((item) => (
                    <option key={item.model} value={item.model}>
                      {item.label || item.model}
                      {item.note ? ` · ${item.note}` : ''}
                    </option>
                  ))}
                </datalist>
                {touched.model && errors.model && <p className="field-error">{errors.model}</p>}
                {modelsNote && <p className="settings-note">模型目录不可用（{modelsNote}），可手动填写模型名。</p>}
              </div>

              <div className="settings-field">
                <label className="settings-label" htmlFor="settings-key">
                  API Key
                  <span className="settings-optional settings-optional-on">只保存在本机 localStorage</span>
                </label>
                <div className="settings-key">
                  <input
                    id="settings-key"
                    className="field"
                    type={showKey ? 'text' : 'password'}
                    value={draft.apiKey}
                    onChange={(event) => patch({ apiKey: event.target.value })}
                    placeholder="sk-…（留空则沿用后端环境变量）"
                    spellCheck="false"
                    autoComplete="off"
                  />
                  <button type="button" className="settings-eye" onClick={() => setShowKey((prev) => !prev)} aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}>
                    <Icon name={showKey ? 'EyeOff' : 'Eye'} className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {thinkingAvailable && (
                <div className="settings-field">
                  <span className="settings-label">推理选项</span>
                  <div className="settings-row">
                    <button
                      type="button"
                      className={`toggle ${draft.thinking ? 'toggle-on' : ''}`}
                      onClick={() => patch({ thinking: !draft.thinking })}
                      aria-pressed={draft.thinking}
                    >
                      <span className="toggle-thumb" />
                      thinking
                    </button>
                    <span className="settings-note-inline">开启后模型会先推理再输出，更慢但更稳。</span>
                  </div>
                  <div className="effort-row" role="group" aria-label="推理强度">
                    {REASONING_EFFORTS.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`effort-chip ${draft.reasoningEffort === item.id ? 'effort-chip-active' : ''}`}
                        onClick={() => patch({ reasoningEffort: item.id })}
                        title={item.hint}
                      >
                        {item.name}
                      </button>
                    ))}
                    <span className="settings-note-inline">推理强度（thinking 开启时生效）</span>
                  </div>
                </div>
              )}

              <div className="test-row">
                <button type="button" className="btn" onClick={runTest} disabled={testing}>
                  <Icon name={testing ? 'Refresh' : 'Zap'} className={testing ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
                  {testing ? '正在测试…' : '测试连接'}
                </button>
                {testResult && (
                  <div className={`test-result ${testResult.ok ? 'test-result-ok' : 'test-result-bad'}`}>
                    <Icon name={testResult.ok ? 'Check' : 'AlertCircle'} className="h-3.5 w-3.5 shrink-0" />
                    <span>
                      {testResult.ok ? `${testResult.message}${testResult.latency ? ` · ${testResult.latency}ms` : ''}` : testResult.message}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <span className="settings-icon">
                <Icon name="Shield" className="h-4 w-4" />
              </span>
              <div>
                <h3 className="settings-card-title">质量控制</h3>
                <p className="settings-card-sub">译后质检模式决定速度与稳妥程度，默认「标准」。</p>
              </div>
              <span className="settings-pill">{QUALITY_MODES.find((item) => item.id === draft.qualityMode)?.name || '标准'}</span>
            </div>
            <div className="settings-card-body">
              <div className="quality-grid" role="radio" aria-label="质量模式">
                {QUALITY_MODES.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="radio"
                    aria-checked={draft.qualityMode === item.id}
                    className={`quality-option ${draft.qualityMode === item.id ? 'quality-option-active' : ''}`}
                    onClick={() => patch({ qualityMode: item.id })}
                  >
                    <span className="quality-option-head">
                      <strong>{item.name}</strong>
                      {draft.qualityMode === item.id && <Icon name="Check" className="h-3 w-3" />}
                    </span>
                    <span className="quality-option-hint">{item.hint}</span>
                  </button>
                ))}
              </div>
              <p className="settings-note">{QUALITY_MODES.find((item) => item.id === draft.qualityMode)?.detail}</p>

              <div className="settings-row">
                <label className="check-row">
                  <input type="checkbox" checked={Boolean(draft.useCache)} onChange={(event) => patch({ useCache: event.target.checked })} />
                  <span className="check-box">
                    <Icon name="Check" className="h-2.5 w-2.5" />
                  </span>
                  <span>复用翻译缓存（相同模型与文本会跳过重复请求）</span>
                </label>
              </div>

              <div className="settings-field">
                <label className="settings-label" htmlFor="settings-target-lang">
                  目标语言
                  <span className="settings-optional">本期仅支持简体中文</span>
                </label>
                <select id="settings-target-lang" className="field" value="zh" disabled>
                  <option value="zh">简体中文</option>
                </select>
              </div>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card-head">
              <span className="settings-icon">
                <Icon name="Cpu" className="h-4 w-4" />
              </span>
              <div>
                <h3 className="settings-card-title">高级</h3>
                <p className="settings-card-sub">本地后端地址与运行信息。</p>
              </div>
              <span className="settings-pill">v{APP_VERSION}</span>
            </div>
            <div className="settings-card-body">
              <div className="settings-field">
                <label className="settings-label" htmlFor="settings-api-base">
                  本地后端地址
                  <span className="settings-optional">需要重启应用生效</span>
                </label>
                <input
                  id="settings-api-base"
                  className={`field ${touched.apiBase && errors.apiBase ? 'field-invalid' : ''}`}
                  value={apiBase}
                  onChange={(event) => setApiBase(event.target.value)}
                  onBlur={() => markTouched('apiBase')}
                  placeholder={DEFAULT_API_BASE}
                  spellCheck="false"
                />
                {touched.apiBase && errors.apiBase && <p className="field-error">{errors.apiBase}</p>}
                <p className="settings-note">默认 http://127.0.0.1:8756，保存后写入本地存储，重启应用生效。</p>
              </div>

              <div className="meta-grid">
                <div className="meta-item">
                  <span className="meta-label">后端版本</span>
                  <span className="meta-value">{health?.version || '未知'}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">翻译引擎</span>
                  <span className="meta-value">{engine.name ? `${engine.name}${engine.version ? ` ${engine.version}` : ''}` : '未知'}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">翻译缓存</span>
                  <span className="meta-value">{cache.files ? `${cache.files} 项 · ${formatBytes(cache.bytes)}` : '无数据'}</span>
                </div>
                <div className="meta-item">
                  <span className="meta-label">OCR</span>
                  <span className="meta-value">{health?.ocr?.available ? health.ocr.engine || '可用' : '不可用（扫描版暂不支持）'}</span>
                </div>
              </div>

              <div className="feature-row">
                {Object.entries(features).length === 0 && <span className="settings-note">后端未上报能力列表。</span>}
                {Object.entries(features).map(([key, value]) => (
                  <span key={key} className={`feature-chip ${value ? 'feature-chip-on' : 'feature-chip-off'}`}>
                    {value ? <Icon name="Check" className="h-2.5 w-2.5" /> : <Icon name="Close" className="h-2.5 w-2.5" />}
                    {key}
                  </span>
                ))}
              </div>
            </div>
          </section>
        </div>

        <footer className="settings-foot">
          <button type="button" className="btn btn-ghost" onClick={resetDefaults}>
            恢复默认
          </button>
          <div className="flex items-center gap-2">
            <span className="settings-kbd">
              <kbd>Esc</kbd> 取消
            </span>
            <button type="button" className="btn" onClick={onClose}>
              取消
            </button>
            <button type="button" className="btn btn-primary" onClick={handleSave} disabled={hasErrors || saving}>
              {saving ? '保存中…' : '保存设置'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}
