# Changelog

本文件记录 paperex 的重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

- 无（v1.2.0 之后的变更将记录在此）。

## [1.2.0] - 2026-11-15

主题：**开箱即用的 Windows 安装包**。本地后端随安装包一起分发，用户无需 Python / Node / 命令行。

### Added

- **Windows 安装包（NSIS）**：`paperex_<version>_x64-setup.exe`，内置 PyInstaller 冻结的本地后端（含 Python 运行时、PyMuPDF、中文字体与 1066 条术语库）。按当前用户安装到 `%LOCALAPPDATA%\paperex`，中英文安装界面，卸载不留残余。
- **后端打包链**：`backend/packaging/` —— `entry.py` 双模式入口（API 服务 / `--qa-worker`）、`paperex-server.spec`、`build_backend.py`、`smoke_test.py`。冻结包同时承担隔离 QA 子进程，安装环境不需要系统 Python。
- **引擎指纹烘焙**：构建时把开发树 `engine_fingerprint()` 写入 `assets/engine_revision.txt`；安装版与源码树共享同一缓存 bucket 与 `qa.json` 的 `engine.revision`，避免冻结后源码缺失导致指纹退化。
- **启动器内置后端探测**：Tauri 启动时先在源码树找 `backend/main.py`（开发模式），找不到再运行安装目录 `backend/paperex-server.exe`（安装包模式）；仍保留端口占用探测与退出时 kill 后端。
- **一键发布工作流**：`.github/workflows/release.yml` —— 推送 `v*` tag 触发 `windows-latest`：冻结后端 → 冒烟测试 → `tauri build` → 上传安装包与 `SHA256SUMS.txt` → 自动创建 GitHub Release；也支持手动 `workflow_dispatch` 只产出 artifact。
- **文档**：新增 `docs/RELEASING.md`（本地打包、静默安装验证、版本号清单、Release 流程、常见问题）；README 新增「安装（Windows，推荐）」章节与下载入口。

### Changed

- **版本号 1.2.0**：`backend/main.py`、`frontend/package.json`、`frontend/src-tauri/tauri.conf.json`、`frontend/src-tauri/Cargo.toml`。
- **Tauri bundle 配置**：`targets` 收敛为 `nsis`；资源映射 `backend/dist/paperex-server/ → backend/`；新增 publisher / homepage / license / category / 中英文安装界面 / currentUser 安装模式。
- **README**：构建章节拆成「本地开发构建」与「构建安装包」两条路径。

### Fixed

- 无（本版本不改变翻译与排版行为；引擎文件未改动，`engine_fingerprint` 仅因适配层打包分支变化而更新）。

## [1.1.0] - 2026-11-11

主题：**质量循环、任务可观测、可恢复与缓存**。v1.1.0 保持 v1.0 的 API 向后兼容，同时显著增强对「翻译质量是否可信」的量化与自愈能力。

### Added

- **质量循环（Quality Loop）**：新增 `quality_mode`（`off` / `fast` / `standard` / `strict`）。standard 最多 1 轮修复；strict 最多 `PAPEREX_QA_MAX_PASSES` 轮修复（默认 4，上限 8）；只有候选评分 `(error_count, issue_count)` 字典序严格变好才接受，否则回滚。
- **隔离 QA 子进程**：`qa_worker.py` 独立进程执行译后检测，崩溃/超时（默认 600 s）降级为 `qa_failed` 警告，不使翻译任务失败。
- **质量报告 `qa.json`（schema v1）**：记录模式、`pass_history`、issue 明细、术语与缓存统计、分阶段耗时；新增 `GET /api/jobs/{id}/qa`。
- **任务阶段与 ETA**：任务响应新增 `stage`、`stage_label`、`progress`、`pages_done/pages_total`、`eta_seconds`、`quality_round`、`quality_mode`、`can_cancel`、`timings_ms`。
- **取消任务**：`POST /api/jobs/{id}/cancel`；基于取消令牌在翻译/QA/写回阶段安全中断，状态置为 `cancelled`。
- **任务持久化与恢复**：`job.json` 原子写、任务心跳与 60 s 超时检测；`PAPEREX_RESUME_JOBS=1` 时进程重启后恢复未完成任务。
- **历史记录增强**：历史项新增 `qa_summary`、`quality_mode`、`elapsed_ms`、`pages`、`blocks`，可在 UI 中直接看到 QA 徽标与失败原因。
- **全局翻译缓存**：按 `protocol|api_url|model|source_lang|target_lang|engine_revision|prompt_version|terms_fingerprint` 分桶的 JSONL 缓存；容量上限（默认 500 MB，`PAPEREX_CACHE_MAX_MB`），命中/未命中/回退块统计写入 `qa.json` 与任务结果。
- **OCR 能力探测与结构化错误**：`PAPEREX_OCR=off|auto|force`、`PAPEREX_TESSDATA`；扫描件无文本层时返回 `ocr_required`，OCR 引擎不可用时返回 `ocr_unavailable`，并给出可执行 `hint`。
- **模型目录与连接测试**：`GET /api/models` 返回供应商与推荐模型；`POST /api/test-connection` 校验 base_url / model / api_key（不会持久化 Key）。
- **术语库 API**：内置 1066 条中英术语，`GET /api/terminology` 与 `GET /api/terminology/search?q=&limit=`；术语快照指纹参与缓存分桶。
- **文件下载**：`GET /api/files/{rel}?download=1` 强制以附件形式下载译文。
- **健康检查扩展**：`/api/health` 返回版本、引擎信息、供应商、质量配置、功能开关、缓存统计与 OCR 状态。
- **前端能力**：阶段进度与 ETA、取消按钮、结构化错误卡片（含「检查设置」「重试」）、QA 报告抽屉（按严重度分组、页码跳转、原始 JSON）、设置页（质量模式 / 模型 / 测试连接 / 缓存统计）、历史 QA 徽标、双栏阅读器与六套强调色 × 深浅主题。
- **文档与仓库治理**：新增 `docs/`（架构、质量、开发、路线图）、`CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`、`.github/` CI 与 issue/PR 模板、`NOTICE`、截图资源；README 全面重写。

### Changed

- **许可证由 MIT 改为 AGPL-3.0-or-later**（**破坏性变更**）：随仓库分发的翻译引擎派生自 AGPL-3.0 的 super_translate 与 PDFMathTranslate/pdf2zh 管线。自 v1.1.0 起全仓以 AGPL-3.0-or-later 发布，详情见 `LICENSE` 与 `NOTICE`。此前 MIT 声明不再适用。
- **产物目录规范化**：`output/<job_id>/` 下统一为 `source.pdf`、`<原文件名>.zh.pdf`、`meta.json`、`qa.json`、`translation-cache.jsonl`、`.snapshots/round-<n>/`、`.qa/`；任务状态 `job.json` 移到 `jobs/<job_id>/`。v1.0 的 `translated.pdf` 命名不再使用（仍可通过 API 的 `files.translated_url` 访问译文）。
- **任务结果字段扩展**：保留 v1.0 字段（`original_url`、`translated_url`、`pages`、`blocks`、`elapsed_ms` 等），新增 `qa_summary`、`files`、`timings_ms`、`error` 结构化对象。
- **错误处理统一**：任务失败改为稳定错误码（`auth_failed`、`provider_unreachable`、`invalid_pdf`、`ocr_required`、`too_large` 等）+ 中文 message + hint，前端错误卡片据此展示。
- **质量默认开启**：v1.0 的 `PAPEREX_POST_QA` 默认关闭；v1.1 起 QA 默认开启（`quality_mode=standard`），可用 `off` 关闭。
- **前端状态持久化键**：主题、模式、设置等迁移到统一的 `paperex.*` 键，旧键自动迁移；样式拆分到组件与 `lib/`、`hooks/`。

### Fixed

- **D1 数字/单位原子化**：修复跨行数字与单位（如 `1.37×`、`17.87×`）被拆分为多个翻译单元、以及公式还原时混入多余空格的问题。
- **D2 脚注 NUL 字符**：修复脚注标记 `∗` 被写成 `\x00`，导致译文出现控制字符与作者信息粘连的问题。
- **D3 保护区英文段落误判**：修复整段英文被错误当作保护区保留、触发 `preserved_ink_mismatch` 的问题。
- **D4 行内公式原子丢失**：修复含变量的行内公式在原子替换时被截断（如 `query` → `uery`）的问题。
- **D5 表格保护区 QA 误报**：修复表格/算法保护区导致的 `untranslated_english` 与 `text_visual_overlap` 误报。
- **D6 字体别名误报**：修复字体别名导致的伪「字体缺失/字体角色不符」告警（规范化 `_FONT_TF_REF`、页面字体资源名与非法别名）。
- **标题拆块重复**：PDF 把一条标题拆成多个块时，模型可能跨块补全导致标题重复（如 OSDI/ATC 封面）。v1.1.0 对疑似标题/子标题的块逐块隔离翻译（单块请求 + 禁补全提示），并把 prompt 版本纳入缓存键。
- **保护区回填英文残片与切段叠印**：减少公式密集页的英文残片（如 `is padded` / `times.` / `to a`）；对公式切段产生的堆叠块做分离排版，降低 `text_overlap`。
- **QA 检测器误报**：图注/图例小框不再误判 `text_outside_frame`；180 dpi 公式光栅改用共享墨迹判定，减少 `raster_ink_overlap` 误报；短 kept-source 块降级为 warning `kept_source_block`，不再计 error。
- **D7 QA 性能**：`verify_translation_issues()` 与 `page_inspector.inspect_translation()` 新增 `pages` 子集参数（0-based，`None` 等价全量），为按受影响页缩小 QA 范围提供基础；当前质量循环复检仍整篇运行，接入留待后续版本。
- 前端：修复缩放后阅读器分页错位/滚动不同步、任务完成后历史不刷新、设置弹窗窄屏溢出等问题。

### Security

- API Key 仅保存在本机浏览器 `localStorage`（Tauri WebView 配置目录），只在请求时发送到用户配置的模型服务；服务端不写入日志与任务产物。
- 任务产物与缓存目录位于用户 `%APPDATA%`，不随仓库分发；`.gitignore` 排除 `new/`、`_work/`、`dist/`、`.venv/`、`target/` 等。
- 新增 `SECURITY.md`，说明漏洞报告渠道与密钥处理边界。

## [1.0.0] - 早期内部版本（无发布日期）

- 初版：PDF 结构分析与保护区识别、擦写式原位重排、CJK 字体嵌入、双栏对照阅读器、任务进度、历史记录、Tauri 桌面壳。
- 翻译服务：DeepSeek / OpenAI 兼容；可选译后质检（`PAPEREX_POST_QA`，默认关闭）。
- 已知局限：扫描件需先 OCR、公式以像素+文本层近似保留、任务串行执行。

[Unreleased]: https://github.com/sylhl/paperex/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/sylhl/paperex/releases/tag/v1.2.0
[1.1.0]: https://github.com/sylhl/paperex/releases/tag/v1.1.0
