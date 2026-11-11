/* 结构化错误映射：error.code / HTTP status / 关键词 / detail → 可恢复的中文错误卡 */

import { BACKEND_START_COMMAND } from '../constants.js'
import { getApiBase } from './storage.js'

const NETWORK_RE = /Failed to fetch|NetworkError|Load failed|ECONNREFUSED|ERR_CONNECTION|fetch failed|Network error/i
const TIMEOUT_RE = /timeout|timed out|ETIMEDOUT|超时/i

/* 每条规则：title 主标题 / hint 建议动作 / action 主按钮 / retryable 是否可重试 */
const RULES = {
  missing_api_key: {
    title: '还没有填写 API Key',
    detail: '当前没有可用的翻译 API Key，无法调用大模型。',
    hint: '打开「设置 → 翻译服务」填写 API Key，或在后端设置环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY。',
    action: 'settings',
    retryable: false
  },
  invalid_base_url: {
    title: '翻译服务地址格式不正确',
    detail: 'base_url 必须是完整的 http(s) 地址，例如 https://api.deepseek.com/v1。',
    hint: '检查设置里的 base_url 是否多了空格、缺了协议头或写成了模型名。',
    action: 'settings',
    retryable: false
  },
  pdf_not_found: {
    title: '找不到这个 PDF',
    detail: '文件可能被移动、重命名或删除了。',
    hint: '重新选择一次文件即可继续，本地文件不会被修改。',
    action: 'repick',
    retryable: true
  },
  invalid_file_type: {
    title: '只支持 PDF 文件',
    detail: '当前选择的文件不是 .pdf，无法解析版面。',
    hint: '请选择扩展名为 .pdf 的英文论文。',
    action: 'repick',
    retryable: false
  },
  file_too_large: {
    title: 'PDF 太大了',
    detail: '本地上传上限为 120MB。',
    hint: '压缩图片或按章节拆分后再试一次。',
    action: 'repick',
    retryable: false
  },
  job_busy: {
    title: '上一个任务还在运行',
    detail: '同一时间只处理一个转换任务。',
    hint: '等当前任务结束，或先取消它再发起新的翻译。',
    action: null,
    retryable: true
  },
  job_not_found: {
    title: '任务已失效',
    detail: '后端重启或任务记录已过期，无法继续查询进度。',
    hint: '完成的译文仍保存在译文库里，可以直接打开；没完成的请重新发起翻译。',
    action: 'library',
    retryable: false
  },
  history_not_found: {
    title: '这条记录已经被删除',
    detail: '译文可能已在别处删除，或后端缓存已清空。',
    hint: '刷新译文库列表即可同步最新状态。',
    action: 'library',
    retryable: false
  },
  auth_failed: {
    title: 'API Key 无效或无权限',
    detail: '翻译服务返回了鉴权失败（401/403）。',
    hint: '检查 API Key 是否过期、是否与 base_url 对应的服务商匹配、账户是否还有额度。',
    action: 'settings',
    retryable: true
  },
  provider_unreachable: {
    title: '连不上翻译服务',
    detail: '无法访问填写的 base_url，可能是网络问题或地址写错。',
    hint: '确认能访问该地址（必要时配置代理），或换用其他兼容服务。',
    action: 'settings',
    retryable: true
  },
  provider_error: {
    title: '翻译服务返回错误',
    detail: '大模型接口返回了非预期结果。',
    hint: '稍后重试；如果持续失败，换一个模型或降低并发/质量档位。',
    action: null,
    retryable: true
  },
  invalid_pdf: {
    title: 'PDF 文件损坏或格式不对',
    detail: '解析器无法读取这个文件的内容。',
    hint: '用阅读器确认文件能正常打开，必要时重新导出 PDF。',
    action: 'repick',
    retryable: false
  },
  encrypted_pdf: {
    title: '这是一个加密 PDF',
    detail: '文件带有打开密码，无法直接解析版面。',
    hint: '先解除密码保护，另存一份未加密的 PDF 再翻译。',
    action: 'repick',
    retryable: false
  },
  ocr_required: {
    title: '这是扫描版 PDF',
    detail: '没有检测到可提取的文本层，需要 OCR 才能翻译。',
    hint: '换一版带文本层的 PDF；或在装有 Tesseract 的机器上启用 OCR 后重试。',
    action: 'repick',
    retryable: false
  },
  ocr_unavailable: {
    title: '本机没有 OCR 引擎',
    detail: '识别扫描版需要 Tesseract，但当前环境没有安装。',
    hint: '安装 Tesseract 并加入 PATH 后重启后端；或换带文本层的 PDF。',
    action: 'repick',
    retryable: false
  },
  no_text_layer: {
    title: '这是扫描版 PDF，暂时无法翻译',
    detail: '页面里只有图片，没有可提取的英文文本。',
    hint: '换一篇有文本层的 PDF，或先用 OCR 工具生成可搜索 PDF。',
    action: 'repick',
    retryable: false
  },
  too_large: {
    title: '文本量超过单次上限',
    detail: '论文正文太长，一次翻译请求装不下。',
    hint: '按章节拆分成多个 PDF 分别翻译，或降低质量档位后重试。',
    action: 'repick',
    retryable: false
  },
  missing_font: {
    title: '缺少中文字体',
    detail: '生成 PDF 时需要中文字体，但后端没找到可用字体文件。',
    hint: '把 .ttf 字体放到 %APPDATA%\\paperex\\fonts\\ 目录，然后重启后端。',
    action: 'copyPath',
    retryable: true
  },
  canceled: {
    title: '任务已取消',
    detail: '这次转换被手动取消，没有生成半成品。',
    hint: '需要的话可以重新发起翻译。',
    action: null,
    retryable: true,
    tone: 'info'
  },
  pdf_load_failed: {
    title: '译文页面载入失败',
    detail: '浏览器无法渲染这个 PDF，可能文件已被删除或损坏。',
    hint: '返回译文库重新打开；如果仍然失败，请重新翻译生成。',
    action: 'library',
    retryable: true
  },
  engine_error: {
    title: '翻译引擎出错',
    detail: '版面回填或 PDF 生成阶段出现异常。',
    hint: '重试一次；持续失败时把详情复制给开发者排查。',
    action: null,
    retryable: true
  },
  qa_failed: {
    title: '译后质检失败，但译文已生成',
    detail: '质检子进程异常退出，不影响已生成的 PDF。',
    hint: '可以正常阅读译文；想重新质检可换质量档位后重跑。',
    action: null,
    retryable: false
  },
  backend_unreachable: {
    title: '无法连接本地后端',
    detail: '',
    hint: '',
    action: 'copyCmd',
    retryable: true
  },
  internal: {
    title: '后端内部错误',
    detail: '处理过程中出现未预期的异常。',
    hint: '重试一次；持续失败请复制详情反馈给开发者。',
    action: null,
    retryable: true
  },
  http_error: {
    title: '请求被后端拒绝',
    detail: '',
    hint: '重试一次；如果持续失败，复制详情便于排查。',
    action: null,
    retryable: true
  },
  unknown: {
    title: '转换失败',
    detail: '',
    hint: '可以重试一次；若反复失败，复制详情反馈给开发者。',
    action: null,
    retryable: false
  }
}

const KEYWORD_RULES = [
  { re: /未配置|没有.{0,4}API ?Key|api[_ ]?key.{0,12}(未|缺|空)|missing.{0,12}api.?key/i, code: 'missing_api_key' },
  { re: /base[_ ]?url|服务地址|接口地址/i, code: 'invalid_base_url' },
  { re: /加密|encrypted|password/i, code: 'encrypted_pdf' },
  { re: /扫描版|扫描件|no text layer|没有文本层|无可提取文本|ocr/i, code: 'ocr_required' },
  { re: /中文字体|字体缺失|font.*(missing|not found)/i, code: 'missing_font' },
  { re: /120 ?MB|too large|超过.{0,6}(大小|上限)/i, code: 'file_too_large' },
  { re: /文本量|too many (tokens|pages)|内容过长/i, code: 'too_large' },
  { re: /文件不存在|no such file|not found.*pdf|找不到.{0,6}(文件|PDF)/i, code: 'pdf_not_found' },
  { re: /只支持.{0,4}pdf|unsupported file|invalid file type/i, code: 'invalid_file_type' },
  { re: /任务.{0,6}(进行中|未结束|还在)/, code: 'job_busy' },
  { re: /任务.{0,4}(不存在|过期|失效)|job.*not found/i, code: 'job_not_found' },
  { re: /记录.{0,6}(不存在|删除)/, code: 'history_not_found' },
  { re: /认证失败|鉴权|unauthorized|forbidden|invalid api key|401|403/i, code: 'auth_failed' },
  { re: /PDF.{0,6}载入失败|InvalidPDF|MissingPDF/i, code: 'pdf_load_failed' },
  { re: /引擎|engine.*error|回填失败/i, code: 'engine_error' },
  { re: /质检|qa.*(fail|error)/i, code: 'qa_failed' },
  { re: /取消|cancel/i, code: 'canceled' }
]

const STATUS_RULES = {
  400: 'http_error',
  401: 'auth_failed',
  403: 'auth_failed',
  404: 'job_not_found',
  409: 'job_busy',
  413: 'file_too_large',
  415: 'invalid_file_type',
  422: 'http_error',
  500: 'internal',
  502: 'provider_unreachable',
  503: 'provider_unreachable',
  504: 'provider_error'
}

function makeError(code, overrides = {}) {
  const base = RULES[code] || RULES.unknown
  return {
    code,
    title: base.title,
    detail: base.detail,
    hint: base.hint,
    action: base.action,
    retryable: base.retryable,
    tone: base.tone || 'error',
    status: null,
    copyText: '',
    raw: '',
    ...overrides
  }
}

function keywordCode(text) {
  const value = String(text || '')
  if (!value) return null
  for (const rule of KEYWORD_RULES) {
    if (rule.re.test(value)) return rule.code
  }
  return null
}

function backendUnreachable(extra = {}) {
  const apiBase = getApiBase()
  return makeError('backend_unreachable', {
    detail: `无法访问 ${apiBase}。本地后端可能还没启动，或者端口被占用。`,
    hint: `在 backend 目录启动：${BACKEND_START_COMMAND}。如果你改过 API 地址，请到设置里核对。`,
    copyText: `cd backend\n${BACKEND_START_COMMAND}`,
    status: null,
    ...extra
  })
}

/**
 * 把任意错误形态归一化成错误卡可直接渲染的对象。
 * @param {unknown} input Error / string / {code,message,retryable,hint} / ApiError
 * @param {{context?: 'job'|'history'|'upload'|'cancel'|'network'|'reader'|'settings'|'delete', status?: number}} ctx
 */
export function normalizeError(input, ctx = {}) {
  const context = ctx.context || ''

  if (!input) {
    return makeError('unknown', { detail: '没有拿到更多错误信息。', status: ctx.status ?? null })
  }

  // ApiError: { status, data, message }
  const status = Number(ctx.status ?? input.status ?? 0) || null
  const data = input?.data && typeof input.data === 'object' ? input.data : null

  // 结构化 error 对象（后端 error.code / detail.code）
  const structured = data?.error || data?.detail || input?.error || (input && typeof input === 'object' && input.code ? input : null)
  const structuredCode = structured && typeof structured === 'object' ? structured.code : null
  const rawMessage =
    (structured && typeof structured === 'object' && (structured.message || structured.detail)) ||
    data?.message ||
    (typeof data?.detail === 'string' ? data.detail : '') ||
    (typeof input === 'string' ? input : input?.message) ||
    ''
  const message = String(rawMessage || '')

  // 网络类（本地后端不可达）
  if (input?.name === 'TypeError' && NETWORK_RE.test(message)) return backendUnreachable({ raw: message })
  if (NETWORK_RE.test(message)) return backendUnreachable({ raw: message })
  if (context === 'network' && !message) return backendUnreachable({ raw: message })

  // 超时
  if (TIMEOUT_RE.test(message) && !structuredCode) {
    return makeError('provider_error', {
      title: '翻译接口超时',
      detail: message,
      hint: '网络较慢或论文太长，重试一次；持续超时请降低质量档位或换网络。',
      retryable: true,
      status,
      raw: message
    })
  }

  // react-pdf 的载入错误
  if (input?.name === 'InvalidPDFException' || input?.name === 'MissingPDFException' || input?.name === 'UnknownErrorException') {
    return makeError('pdf_load_failed', {
      detail: message || 'PDF 解析器无法读取该文件。',
      status,
      raw: message || input.name
    })
  }

  let code = null
  if (structuredCode && RULES[structuredCode]) code = structuredCode
  if (!code && status && STATUS_RULES[status]) code = STATUS_RULES[status]
  if (!code) code = keywordCode(message)
  if (!code && context === 'history' && status === 404) code = 'history_not_found'
  if (!code) code = status ? 'http_error' : 'unknown'

  // 404 在任务上下文里应提示「任务已失效」
  if (code === 'job_not_found' && context === 'history') code = 'history_not_found'

  const base = makeError(code, {
    status,
    raw: message,
    detail: RULES[code]?.detail || message || '后端没有返回详细原因。'
  })

  // 后端显式 code：优先展示后端中文 message / hint / retryable
  if (structured && typeof structured === 'object') {
    if (structured.message) base.detail = String(structured.message)
    if (structured.hint) base.hint = String(structured.hint)
    if (typeof structured.retryable === 'boolean') base.retryable = structured.retryable
    if (code === 'unknown' && structured.title) base.title = String(structured.title)
  } else if (message && code !== 'job_busy' && code !== 'job_not_found') {
    // 没有结构化 code 时保留后端原文作为详情
    if (base.detail === RULES[code]?.detail) base.detail = message
  }

  if (base.action === 'copyCmd') base.copyText = `cd backend\n${BACKEND_START_COMMAND}`
  if (base.action === 'copyPath') {
    base.copyText = '%APPDATA%\\paperex\\fonts\\'
  }
  return base
}

/* 400 等 HTTP 响应 → 错误对象（api.js 使用） */
export function errorFromResponse(status, data, fallbackMessage) {
  const detail = data?.detail
  const structured =
    (detail && typeof detail === 'object' && (detail.code || detail.message) ? detail : null) ||
    (data?.error && typeof data.error === 'object' ? data.error : null)
  if (structured) {
    return normalizeError({ code: structured.code, message: structured.message, data, status })
  }
  const text =
    (typeof detail === 'string' && detail) ||
    (typeof data?.message === 'string' && data.message) ||
    fallbackMessage ||
    `HTTP ${status}`
  return normalizeError(text, { status })
}

/* 给 toast 用的轻量错误 */
export function noticeFromError(input, ctx) {
  return normalizeError(input, ctx)
}

export { backendUnreachable, STATUS_RULES }
