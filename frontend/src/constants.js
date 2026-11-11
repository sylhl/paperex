/* paperex v1.1 前端常量：存储键、主题、质量档位、任务阶段、QA 标签 */

export const APP_VERSION = '1.1.0'

export const STORAGE_KEYS = {
  apiBase: 'paperex.apiBase',
  settings: 'paperex.settings',
  lastDoc: 'paperex.lastDoc',
  sync: 'paperex.sync',
  split: 'paperex.split',
  theme: 'paperex.theme',
  mode: 'paperex.mode'
}

export const LEGACY_STORAGE_KEYS = {
  apiBase: 'pr.apiBase',
  settings: 'pr.settings',
  lastDoc: 'pr.lastDoc',
  sync: 'pr.sync',
  split: 'pr.split',
  theme: 'pr.theme',
  mode: 'pr.mode'
}

export const DEFAULT_API_BASE = 'http://127.0.0.1:8756'

export const DEFAULT_SETTINGS = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  qualityMode: 'standard',
  thinking: false,
  reasoningEffort: 'high',
  useCache: true,
  targetLang: 'zh'
}

export const QUALITY_MODES = [
  {
    id: 'off',
    name: '关闭',
    hint: '不跑译后质检，最快出稿',
    detail: '只翻译不回检，适合赶时间或已知版面简单的论文。'
  },
  {
    id: 'fast',
    name: '快速',
    hint: '一次自检，只报告问题',
    detail: '跑一轮隔离质检并输出报告，但不自动修复。'
  },
  {
    id: 'standard',
    name: '标准',
    hint: '自检 + 最多 1 轮修复（推荐）',
    detail: '质检发现问题后自动修复一轮，再复核一次。'
  },
  {
    id: 'strict',
    name: '严格',
    hint: '最多 3 轮修复，宁慢求稳',
    detail: '反复质检与修复直到问题收敛，耗时最长、质量最高。'
  }
]

export const QUALITY_MODE_IDS = QUALITY_MODES.map((item) => item.id)

export const PROVIDER_PRESETS = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    native: true,
    note: '官方接口，支持 thinking 与推理强度'
  },
  {
    id: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    native: false,
    note: 'OpenAI 官方接口'
  },
  {
    id: 'custom',
    name: '自定义 / 兼容',
    baseUrl: '',
    model: '',
    native: false,
    note: '任意 OpenAI 兼容服务，需自行填写 base_url 与模型名'
  }
]

export const MODEL_FALLBACKS = [
  { provider: 'deepseek', model: 'deepseek-chat', label: 'DeepSeek Chat', note: '通用对话，性价比高' },
  { provider: 'deepseek', model: 'deepseek-flash', label: 'DeepSeek Flash', note: '更快，适合长文' },
  { provider: 'deepseek', model: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro', note: '质量优先' },
  { provider: 'openai', model: 'gpt-4o-mini', label: 'GPT-4o mini', note: 'OpenAI 轻量' }
]

export const REASONING_EFFORTS = [
  { id: 'low', name: '低', hint: '更快，推理更少' },
  { id: 'high', name: '高', hint: '默认推荐' },
  { id: 'max', name: '最大', hint: '最慢，适合难译论文' }
]

export const LOADING_LINES = [
  '正在努力擦除英文痕迹…',
  '正在给汉字寻找合适的沙发…',
  '正在与 PDF 的坐标系谈判…',
  '正在教排版引擎中文断行…',
  '正在把译文押送回原来的坐标…',
  '正在检查有没有字跑出页面…',
  '正在给段落重新量体裁衣…',
  '正在祈祷行尾不要出现孤零零的标点…',
  '正在核对术语表，别把 attention 译成「注意」…',
  '正在给参考文献留一条活路…'
]

export const PAGE_RENDER_WINDOW = 2
export const DEFAULT_PAGE_RATIO = 1.414
export const MAX_UPLOAD_BYTES = 120 * 1024 * 1024

export const THEMES = [
  { id: 'mint', name: '薄荷', hint: '青绿', swatch: 'linear-gradient(135deg,#86f5e4,#22c9b3)' },
  { id: 'ocean', name: '海盐', hint: '天蓝', swatch: 'linear-gradient(135deg,#8be9ff,#229ed9)' },
  { id: 'peach', name: '蜜桃', hint: '珊瑚', swatch: 'linear-gradient(135deg,#ffc2b4,#f06e5b)' },
  { id: 'lemon', name: '柠檬', hint: '青柠', swatch: 'linear-gradient(135deg,#e8f99a,#b7d62e)' },
  { id: 'grape', name: '葡萄', hint: '紫罗兰', swatch: 'linear-gradient(135deg,#cdbdff,#8b5cf6)' },
  { id: 'rose', name: '玫瑰', hint: '樱粉', swatch: 'linear-gradient(135deg,#ffb6cb,#e11d48)' }
]

export const MODES = [
  { id: 'light', name: '浅色' },
  { id: 'dark', name: '深色' }
]

/* 阶段中文名：后端 stage_label 缺失时本地兜底 */
export const STAGE_LABELS = {
  idle: '准备中',
  queued: '排队中',
  uploading: '上传中',
  parse: '解析 PDF',
  analyzing: '识别版面',
  analyze: '识别版面',
  translate: '翻译正文',
  translating: '翻译正文',
  reflow: '回填版式',
  qa: '译后质检',
  repairing: '质量修复',
  repair: '质量修复',
  finalize: '收尾保存',
  writing: '收尾保存',
  done: '已完成',
  failed: '失败',
  error: '失败',
  canceled: '已取消',
  cancelled: '已取消'
}

/* 进度条步骤（阶段 → 步骤序号） */
export const STAGE_STEPS = [
  { id: 'parse', label: '解析 PDF', hint: '读取页数与文本层' },
  { id: 'analyze', label: '识别版面', hint: '定位段落与公式' },
  { id: 'translate', label: '翻译正文', hint: '逐段调用大模型' },
  { id: 'reflow', label: '回填版式', hint: '把译文放回坐标' },
  { id: 'qa', label: '译后质检', hint: '检查溢出与缺译' },
  { id: 'finalize', label: '收尾保存', hint: '生成双语 PDF' }
]

export const STAGE_STEP_INDEX = {
  queued: -1,
  idle: -1,
  uploading: -1,
  parse: 0,
  analyze: 1,
  analyzing: 1,
  translate: 2,
  translating: 2,
  reflow: 3,
  qa: 4,
  repair: 4,
  repairing: 4,
  finalize: 5,
  writing: 5,
  done: 6,
  failed: 6,
  error: 6,
  canceled: 6,
  cancelled: 6
}

/* QA issue code → 中文短标签（未知 code 原样展示） */
export const QA_CODE_LABELS = {
  untranslated: '未翻译',
  untranslated_text: '残留英文',
  missing_translation: '缺译',
  missing_block: '段落缺失',
  empty_page: '空白页',
  overflow: '文字溢出',
  text_overflow: '文字溢出',
  overlap: '文字重叠',
  clipped: '文字被裁切',
  encoding: '乱码',
  garbled: '乱码',
  font_missing: '字体缺失',
  page_mismatch: '页数不一致',
  page_count_mismatch: '页数不一致',
  heading: '标题样式',
  figure: '图片版式',
  table: '表格版式',
  formula: '公式',
  reference: '参考文献',
  placeholder: '占位符残留',
  punctuation: '标点异常'
}

export const QA_STATUS = {
  passed: { label: '通过', tone: 'ok' },
  ok: { label: '通过', tone: 'ok' },
  warnings: { label: '有警告', tone: 'warn' },
  warning: { label: '有警告', tone: 'warn' },
  failed: { label: '未通过', tone: 'bad' },
  error: { label: '未通过', tone: 'bad' },
  skipped: { label: '未运行', tone: 'muted' },
  not_run: { label: '未运行', tone: 'muted' }
}

export const SHORTCUTS = [
  { keys: ['Ctrl', 'O'], label: '新增翻译（选择 PDF）' },
  { keys: ['Ctrl', ','], label: '打开设置' },
  { keys: ['Ctrl', '='], label: '放大页面' },
  { keys: ['Ctrl', '-'], label: '缩小页面' },
  { keys: ['Ctrl', '0'], label: '恢复 100% / 适配宽度' },
  { keys: ['PageUp', 'PageDown'], label: '上一页 / 下一页' },
  { keys: ['Home', 'End'], label: '跳到首页 / 末页' },
  { keys: ['Esc'], label: '关闭设置、QA 面板或错误提示' },
  { keys: ['?'], label: '打开快捷键帮助' }
]

export const BACKEND_START_COMMAND = 'uvicorn main:app --host 127.0.0.1 --port 8756'
