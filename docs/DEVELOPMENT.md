# 开发指南

本文面向 **paperex 贡献者**：如何搭环境、跑测试、构建发布、定位问题。使用说明见 [README](../README.md)，架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，质量体系见 [QUALITY.md](QUALITY.md)。

## 1. 环境要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Windows | 10/11 x64 | 主力开发与发布平台 |
| Python | 3.12 / 3.13 | CI 矩阵覆盖；开发机使用 3.13 |
| Node.js | ≥ 20（推荐 20 LTS） | 前端构建；开发机可用 22/24 |
| Rust | ≥ 1.77.2 | 仅 Tauri 桌面壳需要（`rustup` 安装 stable） |
| WebView2 | 系统自带 | Tauri 在 Windows 上的渲染后端 |
| VS Build Tools 2022 | C++ 桌面开发负载 | Tauri / Rust 链接需要 |
| Tesseract | 可选 | 扫描件 OCR；不随仓库分发 |

> macOS / Linux 可运行后端与网页前端，但 Tauri 打包路径未验证（见 [ROADMAP.md](ROADMAP.md)）。

## 2. 首次准备

### 2.1 后端

```powershell
cd paperex\backend
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -r requirements.txt
# 贡献者还需要测试/静态检查依赖：
python -m pip install -r requirements-dev.txt
```

`requirements.txt`（运行时）：FastAPI、Uvicorn、PyMuPDF、httpx、python-multipart、pydantic。
`requirements-dev.txt`（开发）：pytest、pytest-asyncio、ruff（`httpx` 已包含在 `requirements.txt`）。

### 2.2 前端

```powershell
cd paperex\frontend
npm install          # 首次；CI 使用 npm ci
```

### 2.3 桌面壳（可选）

```powershell
rustup target add x86_64-pc-windows-msvc   # 一般随 VS Build Tools 已就绪
```

## 3. 日常开发

开两个终端：

```powershell
# 终端 A：后端（自动重载）
cd paperex\backend
.\.venv\Scripts\activate
$env:PYTHONUTF8 = "1"
python -m uvicorn main:app --host 127.0.0.1 --port 8756 --reload
```

```powershell
# 终端 B：前端（Vite，固定 1420，strictPort）
cd paperex\frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:1420`。前端默认后端地址为 `http://127.0.0.1:8756`，也可通过 URL 参数或 `localStorage` 的 `paperex.apiBase` 覆盖。

**UI 开发无需后端**：前端支持 `?demo=empty|home|progress|error|qa|reader` 使用内置演示数据渲染；`?view=settings|qa|help` 直达页面；`?theme=<mint|ocean|peach|lemon|grape|rose>&mode=dark|light` 临时切换主题（不写入设置）。这些参数只影响当前渲染，不持久化。

**无 API Key 联调后端**：

```powershell
$env:PAPEREX_DRYRUN = "1"     # 返回模拟翻译结果，不请求模型
$env:PAPEREX_POST_QA = "1"    # 打开译后 QA（默认开启）
```

Tauri 窗口内开发（会由 Rust 壳自动拉起后端）：

```powershell
cd paperex\frontend
npm run tauri:dev
```

## 4. 测试

### 4.1 后端单元/契约测试

```powershell
cd paperex\backend
.\.venv\Scripts\python.exe -m pytest tests -q
```

按文件：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_quality.py -q        # 质量模式、动作规划、strict gate
.\.venv\Scripts\python.exe -m pytest tests\test_api_contract.py -q   # v1.1 API 字段契约
.\.venv\Scripts\python.exe -m pytest tests\test_engine_regression.py -q  # QA 检测器（离线 fixture）
.\.venv\Scripts\python.exe -m pytest tests\test_jobs.py -q           # 任务状态机/取消/持久化
.\.venv\Scripts\python.exe -m pytest tests\test_cache.py -q          # 翻译缓存键与命中
.\.venv\Scripts\python.exe -m pytest tests\test_qa_worker.py -q      # 子进程协议/超时/崩溃降级
```

> 标了 `slow` / `e2e` 的用例需要真实 PDF 或模型服务，默认跳过；CI **不调用真实 LLM**。

### 4.2 引擎离线自测（无需 API Key）

```powershell
cd paperex\backend
.\.venv\Scripts\python.exe tests\test_engine_offline.py
```

该脚本直接调用 `pdf_zh_translator`，验证结构分析、保护区、重排、QA 检测器在固定 fixture 上的行为。

### 4.3 前端构建检查

```powershell
cd paperex\frontend
npm run build
```

### 4.4 手工端到端

1. `PAPEREX_DRYRUN=1` 启动后端，准备一篇带文本层的 PDF（扫描件需先 OCR）；
2. 新建任务 → 观察阶段（解析/翻译/QA/修复/写回）与 ETA；
3. 完成后打开双栏阅读器，检查 QA 徽标与 QA 抽屉；
4. 在任务运行中测试「取消」；刷新页面验证历史与任务恢复；
5. 检查 `%APPDATA%\paperex\output\<job_id>\` 下的 `meta.json`、`<原文件名>.zh.pdf`、`qa.json`，以及 `%APPDATA%\paperex\jobs\<job_id>\job.json`。

## 5. 代码规范

- 后端：`ruff check backend` 必须通过（CI 强制）。配置见仓库根 `ruff` 默认规则；不要用 `# noqa` 掩盖真实问题。
- 前端：暂无 ESLint/Prettier 配置，遵循周边代码风格（2 空格缩进、函数组件 + hooks、中文 UI 文案）；不要引入新的运行时依赖，除非有明确必要并与维护者讨论。
- 文档：中文为主；命令用 PowerShell 语法（Windows 主平台），跨平台命令另注说明。
- 提交：见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 6. 构建与发布

### 6.1 前端静态产物

```powershell
cd paperex\frontend
npm run build        # 输出 frontend\dist\
npm run preview      # 本地预览 dist，默认 http://127.0.0.1:4173
```

### 6.2 Windows 桌面程序

```powershell
cd paperex\frontend
npm run tauri:build -- --no-bundle
# 产物：frontend\src-tauri\target\release\paperex.exe
```

把 `paperex.exe` 复制到 `paperex\launcher\`（`launcher\paperex.exe` 与 `paperex.lnk` 不入库）。**不要**直接用 `cargo build --release`：缺少 Tauri 的 `custom-protocol` feature 时程序会尝试连接 `http://127.0.0.1:1420` 而不是加载内嵌前端。

桌面壳启动时会：

1. 探测 `127.0.0.1:8756` 是否已有后端在运行；
2. 若没有，从 exe 所在目录向上查找含 `main.py` 的 `backend\`，优先使用 `backend\.venv\Scripts\python.exe`，否则用 `python` 启动 `uvicorn main:app --host 127.0.0.1 --port 8756`；
3. 窗口关闭或应用退出时终止自己拉起的后端子进程。

因此**发布目录必须同时包含 `backend\`（代码、字体、`.venv` 或可用的 Python 环境）**，不能只分发单个 exe。

### 6.3 发布检查清单

- [ ] 版本号三处一致：`backend\main.py` 的 `APP_VERSION`、`frontend\package.json` 的 `version`、`frontend\src-tauri\tauri.conf.json` 的 `version`
- [ ] `CHANGELOG.md` 增加对应版本小节（Keep a Changelog 格式）
- [ ] `pytest backend\tests -q`、`ruff check backend`、`npm run build` 均通过
- [ ] `README.md` 实测数据表按最终基线更新
- [ ] 许可证/第三方声明（`LICENSE`、`NOTICE`、字体 OFL 文件）完整
- [ ] 不提交 `new/`、`_work/`、`dist/`、`.venv/`、`target/`、`launcher\*.exe`

## 7. 调试

| 现象 | 排查 |
| --- | --- |
| 前端提示无法连接后端 | `curl http://127.0.0.1:8756/api/health`；确认端口 8756 未被占用；检查 `paperex.apiBase` |
| 任务一直 `queued` | 任务串行执行（引擎全局锁），看前一个任务；检查后端日志 |
| QA 很慢 | 正常现象：QA 需渲染页面做像素对比；用 `quality_mode=fast/off` 或降低 `PAPEREX_QA_MAX_PASSES` |
| 报 `ocr_required` | 扫描件无文本层；安装 Tesseract 并设置 `PAPEREX_TESSDATA`，或先外部 OCR |
| 报 `auth_failed` | 检查 API Key、模型名与 base_url；用设置页「测试连接」 |
| 翻译结果像是没变 | `PAPEREX_DRYRUN` 是否还开着 |
| 桌面版打不开页面 | 是否用 `cargo build --release` 构建；应使用 `npm run tauri:build` |
| 需要查看任务产物 | `%APPDATA%\paperex\output\<job_id>\`（`meta.json` / `qa.json` / `translation-cache.jsonl` / `<原文件名>.zh.pdf`）与 `%APPDATA%\paperex\jobs\<job_id>\job.json` |
| 需要看原始 QA issue | `GET /api/jobs/{job_id}/qa`，或直接打开 `qa.json` |

调试开关（环境变量）：`PAPEREX_DRYRUN`、`PAPEREX_QA_MODE`、`PAPEREX_POST_QA`、`PAPEREX_QA_MAX_PASSES`、`PAPEREX_QA_STRICT_CODES`、`PAPEREX_OCR`、`PAPEREX_TESSDATA`、`PAPEREX_RESUME_JOBS`、`PAPEREX_CACHE_DIR`。完整列表见 README「环境变量」。

日志：后端 `uvicorn` 输出到终端；桌面版 release 构建隐藏控制台，排查时用 `npm run tauri:dev`。

## 8. CI

`.github/workflows/ci.yml` 在每次 push / PR 运行：

| Job | 运行环境 | 步骤 |
| --- | --- | --- |
| `backend` | `ubuntu-latest` + Python `3.12` | 安装 `requirements.txt` + `requirements-dev.txt` → `ruff check .` → `pytest tests -q` |
| `frontend` | `ubuntu-latest` + Node `20` | `npm ci` → `npm run build` |

CI 不调用真实 LLM，不下载论文；需要真实服务的 e2e 用例默认跳过。本地复现 CI：

```powershell
cd paperex\backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
ruff check .

cd ..\frontend
npm ci
npm run build
```

## 9. 目录速查

```
paperex/
├─ backend/                 FastAPI 服务、引擎适配、QA
│  ├─ main.py               HTTP 路由、任务调度、产物管理
│  ├─ engine_adapter.py     模型供应商/字体/翻译器装配、issue 汇总
│  ├─ quality.py            QA 纯逻辑（分类、评分、修复规划）
│  ├─ qa.py / qa_worker.py   QA 编排/报告与独立子进程入口
│  ├─ qa_loop.py            质量循环调度（轮数、时间预算、接受/回滚）
│  ├─ jobs.py / cache.py    任务状态机与持久化 / 全局翻译缓存
│  ├─ providers.py / ocr.py 模型目录与连通性测试 / OCR 探测与预处理
│  ├─ pdf_zh_translator/    同源引擎（AGPL-3.0，见 NOTICE）
│  ├─ assets/fonts/         LXGW 字体 + OFL 许可证
│  └─ tests/                pytest 与离线 fixture
├─ frontend/                React 18 + Vite + Tailwind
│  ├─ src/App.jsx           顶层状态与视图编排（components/ 为子组件）
│  ├─ src/constants.js      主题、模式、默认配置
│  └─ src-tauri/            Tauri 2 桌面壳（Rust）
├─ launcher/                已构建 exe（不入库）
├─ docs/                    架构/质量/开发/路线图
└─ .github/                 CI 与 issue/PR 模板
```
