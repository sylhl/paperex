/* 开发/截图用演示数据与 URL 直达参数（?demo= ?view= ?theme= ?mode= ?apiBase=） */

import originalPdf from './assets/demo-original.pdf?url'
import translatedPdf from './assets/demo-translated.pdf?url'

const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : new URLSearchParams()

export const urlParams = params
export const demoMode = params.get('demo') || ''
export const initialView = params.get('view') || ''

export function urlApiBase() {
  const value = params.get('apiBase')
  return value ? value.replace(/\/+$/, '') : ''
}

export function urlAppearance() {
  const theme = params.get('theme')
  const mode = params.get('mode')
  return {
    theme: ['mint', 'ocean', 'peach', 'lemon', 'grape', 'rose'].includes(theme) ? theme : '',
    mode: mode === 'light' || mode === 'dark' ? mode : ''
  }
}

const absolute = (value) => {
  try {
    return new URL(value, window.location.href).href
  } catch {
    return value
  }
}

export const DEMO_HISTORY = [
  {
    job_id: 'demo-1',
    name: '长文档翻译示例：论文版式重排.pdf',
    created_at: Math.floor(Date.now() / 1000) - 3600 * 26,
    stats: { pages: 15, blocks: 420, translated_blocks: 418, skipped_blocks: 2, elapsed_ms: 96400, font: 'NotoSerifSC', engine: 'paperex-reflow' },
    qa_summary: { errors: 1, warnings: 3, total: 4, status: 'failed', passes_run: 2, repair_attempted: true },
    quality_mode: 'standard',
    translated_url: '/api/files/demo-1/translated.pdf',
    original_url: '/api/files/demo-1/source.pdf'
  },
  {
    job_id: 'demo-2',
    name: '示例论文：图像分类模型的对照实验.pdf',
    created_at: Math.floor(Date.now() / 1000) - 3600 * 50,
    stats: { pages: 12, blocks: 356, translated_blocks: 356, skipped_blocks: 0, elapsed_ms: 71200 },
    qa_summary: { errors: 0, warnings: 0, total: 0, status: 'passed', passes_run: 1 },
    quality_mode: 'fast',
    translated_url: '/api/files/demo-2/translated.pdf',
    original_url: '/api/files/demo-2/source.pdf'
  },
  {
    job_id: 'demo-3',
    name: '扫描版示例：旧论文影印页.pdf',
    created_at: Math.floor(Date.now() / 1000) - 3600 * 73,
    stats: { pages: 8, blocks: 0, translated_blocks: 0, skipped_blocks: 0 },
    qa_summary: { errors: 0, warnings: 0, total: 0, status: 'skipped', skipped: true },
    failed: true,
    error: { code: 'ocr_required', message: '扫描版 PDF：没有检测到可提取的文本层', retryable: false },
    quality_mode: 'standard'
  }
]

export const DEMO_QA_REPORT = {
  schema_version: 1,
  job_id: 'demo-1',
  generated_at: Math.floor(Date.now() / 1000) - 3500,
  mode: 'standard',
  status: 'failed',
  passes_run: 2,
  repair_attempted: true,
  score: { errors: 1, warnings: 3, total: 4 },
  best_score: { errors: 1, warnings: 3, total: 4 },
  engine: { name: 'paperex-qa', revision: '1.1.0' },
  pass_history: [
    { pass: 0, errors: 2, warnings: 7, total: 9 },
    { pass: 1, errors: 1, warnings: 3, total: 4 }
  ],
  issues: [
    {
      page: 4,
      code: 'overflow',
      severity: 'warning',
      message: '第 4 页右栏有一段译文超出当前文本块 2.1pt，已自动缩小字号回填。',
      action: '可接受：字号仅缩小 1%，阅读不受影响。'
    },
    {
      page: 7,
      code: 'untranslated_text',
      severity: 'warning',
      message: '公式编号 (12) 后残留英文短句 “where α is the learning rate”，属于术语保留。',
      action: '若需要完全汉化，可在术语表中把该短语加入翻译白名单。'
    },
    {
      page: 11,
      code: 'heading',
      severity: 'warning',
      message: '第 11 页小节标题的字重与原文略有差异（粗体 → 中等）。',
      action: '可接受：视觉层级仍然清晰。'
    },
    {
      page: 2,
      code: 'overlap',
      severity: 'error',
      message: '第 2 页图注与页脚行距不足 0.8pt。',
      action: '已在本轮修复中调整行距，复核通过。'
    }
  ],
  skipped: false,
  total: 4,
  errors: 1,
  warnings: 3,
  codes: { overflow: 1, untranslated_text: 1, heading: 1, overlap: 1 }
}

export const DEMO_PROGRESS_JOB = {
  status: 'running',
  stage: 'translate',
  stage_label: '翻译正文',
  progress: 42.6,
  pages_done: 6,
  pages_total: 15,
  eta_seconds: 86,
  queue_position: 0,
  quality_round: 1,
  quality_mode: 'standard',
  can_cancel: true,
  message: '正在翻译第 6/15 页',
  files: {},
  timings_ms: { translate: 41200, total: 41200 },
  log: [
    '22:01:37  解析完成：15 页，420 个文本块',
    '22:01:39  版面分析完成：单栏 / 双栏混合',
    '22:01:44  开始翻译：模型 deepseek-chat',
    '22:02:19  译文批次 4/10 回填完成',
    '22:02:55  正在翻译第 6/15 页'
  ]
}

export const DEMO_READER_DOC = {
  jobId: 'demo-1',
  name: '长文档翻译示例：论文版式重排.pdf',
  original: absolute(originalPdf),
  translated: absolute(translatedPdf),
  stats: {
    pages: 2,
    blocks: 42,
    translated_blocks: 42,
    skipped_blocks: 0,
    font: 'NotoSerifSC',
    engine: 'paperex-reflow',
    elapsed_ms: 96400,
    quality_mode: 'standard',
    qa: DEMO_QA_REPORT
  }
}
