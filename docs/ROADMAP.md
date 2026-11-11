# 路线图

本文件记录 paperex 的已交付能力与下一步方向。**计划不是承诺**：优先级会随用户反馈与实际缺陷调整；每项进入开发前会先在 issue 中讨论。

## v1.1.0（已交付，2026-11-10）

主题：**从「能翻译」到「可信赖、可观测、可自愈」**。

- **质量循环**：`quality_mode`（off / fast / standard / strict）、隔离 QA 子进程、严格变好才接受候选、逐轮快照与回滚。
- **质量报告**：`qa.json`（schema v1）、`GET /api/jobs/{id}/qa`、前端 QA 抽屉（分组、页码跳转、原始 JSON）。
- **任务可观测与可控**：阶段与阶段标签、`pages_done/pages_total`、`eta_seconds`、质量轮次、取消（`POST /api/jobs/{id}/cancel`）、重试与结构化错误码。
- **任务持久化**：`job.json` 原子写、心跳与进程重启后的任务恢复（`PAPEREX_RESUME_JOBS`）、历史记录带 QA 摘要。
- **翻译缓存**：按协议/模型/语言/引擎修订/术语指纹分桶的全局缓存，容量上限与命中统计。
- **OCR 提示**：无文本层时给出结构化 `ocr_required` / `ocr_unavailable`，支持 `PAPEREX_OCR=off|auto|force`。
- **模型目录**：`GET /api/models`、设置页模型选择与「测试连接」（`POST /api/test-connection`）。
- **术语库**：内置 1066 条中英术语，`GET /api/terminology` 与检索接口；术语指纹参与缓存分桶。
- **前端 UX**：阶段/ETA/取消、错误卡片、QA 徽标与报告、双栏阅读器、六强调色深浅主题、窄屏适配。
- **引擎修复**：D1 数字/单位原子化、D2 脚注 NUL 字符、D3 保护区英文段落误判、D4 行内公式丢失、D5 表格保护区 QA 误报、D6 字体别名误报、D7 verify/inspect 页子集参数（增量复检基础，尚未接入质量循环）。

## v1.2（近期方向）

- **QA 误报治理**：`high_risk_layout` 等高频 warning 的分级与抑制策略；对已确认误报建立回归 fixture，持续降低误报率。
- **增量 QA**：让质量循环复检只跑受影响页（接入 D7 的 `pages` 参数），把 `strict` 的总耗时压下来。
- **质量报告可读性**：QA 抽屉按页面聚合、issue 说明中文化与修复建议；报告分享（导出 HTML/Markdown）。
- **术语库编辑**：设置页增删改查术语、导入/导出 CSV、冲突提示；术语变更自动更新缓存分桶。
- **OCR 体验**：更清晰的安装引导与 `PAPEREX_TESSDATA` 探测；评估随安装包分发 Tesseract 的可行性与许可证影响。
- **错误自愈**：网络类错误（`provider_unreachable` / 5xx）自动退避重试；断点续跑更细粒度（按页/按块）。

## v1.3（中期方向）

- **公式保真增强**：在现有「像素 + 文本层」基础上评估公式语义保留（LaTeX/MathML 旁路），提升无障碍与可检索性。
- **排版能力**：复杂多栏、跨页表格、图表标题编号的更强结构感知；更多期刊模板 profile。
- **并发与吞吐**：从单引擎锁串行走向受控并发（多任务队列 + 资源配额），同时保证内存与质量稳定。
- **打包与分发**：Windows 安装包（MSI/NSIS）、后端依赖冻结方案、自动更新通道。
- **跨平台**：macOS / Linux 的 Tauri 构建验证与 CI 覆盖（后端与网页版优先）。

## 探索 / 待验证

- Tesseract 随包分发与许可证合规（Apache-2.0，但二进制体积与语言包需评估）。
- 更大模型（`deepseek-v4-pro` 等）在长文上的质量/成本曲线，以及 `reasoning_effort` 的默认策略。
- 扫描版论文的版面重建（OCR 后重排）质量上限。
- 论文原文不进入模型上下文的隐私增强方案（本地预处理、字段脱敏）。

## 非目标

- 不做论文代写、改写或降重；paperex 只做**翻译与版式保留**。
- 不内置论文下载/爬取功能；不提供绕过付费墙的能力。
- 不追求「无人值守全自动投稿」：QA 是辅助，最终质量由作者负责。
- 不承诺与所有 PDF 阅读器/编辑器 100% 兼容；以 PyMuPDF 渲染与 Adobe/Edge 阅读器为准。

## 如何影响路线图

- 提 issue：使用 `.github/ISSUE_TEMPLATE/feature_request.yml`，说明使用场景与期望结果。
- 带数据：附论文类型、页数、`qa.json` 摘要（可脱敏）、复现步骤，能显著提高优先级。
- 参与开发：见 [CONTRIBUTING.md](../CONTRIBUTING.md) 与 [DEVELOPMENT.md](DEVELOPMENT.md)。
