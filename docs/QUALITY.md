# 质量体系与 QA 报告

paperex 的「质量」不是一句口号，而是一套**可复现、可机器读取、可自动修复、可降级**的流程：翻译落盘后运行两层检测器，产出 `qa.json`；质量模式决定是否进入修复循环；每一轮修复都必须严格变好才被接受。

> 重要边界：QA 是启发式检测，**不等于人工校对**。`strict` 通过只代表「自动检测层面干净」，公式语义、术语准确性与学术表达仍需作者复核。

## 1. 质量模式

`quality_mode` 通过 `POST /api/jobs` 传入（应用设置页可改默认值），默认 `standard`。

| 模式 | QA 次数 | 自动修复 | 通过条件 | 相对耗时 | 适用场景 |
| --- | --- | --- | --- | --- | --- |
| `off` | 0 | — | 不检测 | 最快 | 草稿阅读、批量粗翻 |
| `fast` | 1 | 否 | 不设 gate，只报告 | 快（翻译 + 1 次 QA） | 想看质量但不想等待修复 |
| `standard`（默认） | 1–2 | 最多 1 轮 | 有可修复 action 时才尝试，候选严格变好才接受 | 中等 | 日常使用 |
| `strict` | 2–4 | 最多 3 轮 | `error == 0` 且**可处理 warning == 0** | 最慢 | 投稿级自查、重要论文 |

实现约定：

- **`passes_run`** 是实际执行的 QA 次数（含初始检测与每轮修复后的复检）。
- **总 QA 次数上限**由 `PAPEREX_QA_MAX_PASSES` 控制（`1–8`，默认 `4`）。`strict` 的 3 轮修复机会在默认配置下正好对应 4 次 QA。
- **`repair_attempted`** 表示本轮任务是否真正执行过修复动作（`off` / `fast` 恒为 false）。
- **`qa_failed`**：QA 子进程崩溃或超时（默认 600 s）。任务本身不会失败：`qa_summary.status = qa_failed`，翻译产物照常可用，UI 以警告展示。
- **`off`** 不生成 `qa.json`，`GET /api/jobs/{id}/qa` 返回 404。

## 2. 两层检测器

| 检测器 | 入口 | 检查对象 | 典型问题 |
| --- | --- | --- | --- |
| 文本对象级 | `pdf_zh_translator.pdf_layout.verify_translation_issues(original, translated)` | 逐页比对文本对象、图片、链接、页数 | 漏翻、占位符泄漏、公式/图片缺失、页数或页面尺寸不一致、空白页、保护区内容被改 |
| 页面级视觉 | `pdf_zh_translator.page_inspector.inspect_translation(...)` | 渲染原文页与译文页，做像素与几何对比（原文是 ground truth） | 文字重叠、图注重叠、文字压图、字号漂移、表格结构变化、保护区墨迹不匹配、公式被裁切、参考文献被改动 |
| 视觉评分 | `pdf_zh_translator.visual_qa.py` | 页面级 layout 分数与区域分数 | `visual_layout_score_low`、`visual_layout_region_low`、`raster_*` 系列 |

检测器只读产物、不改 PDF；所有结果统一为 `pdf_zh_translator.pdf_layout.TranslationIssue`（`page / code / message / severity`），再汇总进 `qa.json`。

## 3. Issue code 分类

引擎当前约 39 个 issue code。按**修复动作**分为四类（与 `backend/quality.py` 中的集合一一对应）：

### 3.1 阻断级（BLOCKING，无法自动修复）

| code | 含义 | 默认严重度 |
| --- | --- | --- |
| `page_count_mismatch` | 译文页数与原文不一致 | error |
| `page_size_mismatch` | 页面尺寸（宽高）与原文不一致 | error |
| `empty_page` | 出现空白页 | error |
| `qa_open_failed` | 无法打开原文或译文（QA 前置失败） | error |

这些属于结构性失败，质量循环不会尝试修复，只报告并要求人工介入。

### 3.2 可重排修复（LAYOUT_REPAIR）

| code | 含义 | 默认严重度 |
| --- | --- | --- |
| `caption_overlap` | 图注与正文/图注相互重叠 | warning / error（按重叠面积） |
| `text_overlap` | 两段译文文字互相压叠 | warning / error（按重叠面积） |
| `text_visual_overlap` | 译文压到图片/图表区域 | warning |
| `raster_heading_body_overlap` | 标题与正文在栅格渲染层重叠 | error |
| `raster_caption_clearance` | 图注与图表/正文在栅格层间距不足 | error |
| `raster_ink_overlap` | 栅格层墨迹重叠 | error |

修复动作 `repair_layout`：基于上一轮快照重新执行 CJK 排版，`font_scale` 依次降至 `0.86` / `0.82`，打开 `skip_overflow` 与更小 `min_font_size`；不重新请求模型，速度快、不花钱。

### 3.3 可重译修复（RETRANSLATABLE）

| code | 含义 |
| --- | --- |
| `untranslated_english` | 正文英文段落漏翻 |
| `untranslated_caption` | 图注漏翻 |
| `untranslated_formula_explanation` | 公式解释文字漏翻 |
| `untranslated_natural_language` | 自然语言段落漏翻 |
| `untranslated_block` | 整块英文段落未翻译（视觉检测器发现） |

修复动作 `retranslate`：将这些块从缓存中失效并重新翻译（仅相关块，不是整篇），再走重排与复检。会消耗模型 token。

### 3.4 仅报告（STOP）

以下 code 不参与自动修复，计入 `qa.json` 与 UI 提示，由人工判断：

| code 组 | 含义 | 默认严重度 |
| --- | --- | --- |
| `high_risk_layout` | 页面存在公式/图表密集的高风险版式区域 | warning |
| `formula_changed` | 保护区公式的可见文本/绘制发生变化 | warning |
| `formula_visible_ink_mismatch` | 公式精灵存在但可见墨迹缺失 | error |
| `formula_clipped` | 公式字形被渲染边缘裁切 | error / warning（按边缘墨迹密度） |
| `inline_formula_missing` | 行内公式原子丢失 | error |
| `placeholder_leak` | 占位符泄漏到最终文本（如 `⟦3⟧`） | error |
| `preserved_ink_mismatch` | 保护区（表格/算法/展示公式）墨迹与原图不匹配 | error |
| `preserved_text_changed` | 保护区文本被改写 | warning |
| `kept_source_block` | 短块（≤3 行、≥3 词）译文与源文逐字相同，按设计保留源排版（kept-source）；长段仍报 `untranslated_block` | warning |
| `font_size_drift` / `list_font_inconsistent` | 字号相对原文漂移 / 同级列表字号不一致 | error |
| `font_role_bold_spill` / `font_role_heading_mismatch` / `font_role_size_mismatch` / `font_role_title_orphan` | 字体角色（粗体、标题字号、标题孤立）与原文不符 | error |
| `fragmented_prose` | 段落被拆成碎片 | warning |
| `table_structure_mismatch` / `table_cells_reflowed` | 表格结构变化 / 单元格被重排 | error |
| `reference_overlap` / `reference_bold_style` / `reference_content_changed` | 参考文献区域重叠 / 粗体注入 / 内容被翻译 | error / warning |
| `text_outside_frame` | 文字越出版心 | error |
| `display_formula_misaligned` | 展示公式水平位置漂移 | warning |
| `visual_layout_score_low` / `visual_layout_region_low` | 页面整体或局部 layout 评分过低 | error |
| `missing_image` / `missing_graphic` | 图片或矢量图缺失 | error |
| `inspection_failed` | 页面视觉检测自身失败（降级为 warning） | warning |

> 具体严重度由引擎按页与阈值决定，以实际 `qa.json` 为准。`high_risk_layout` 在实测中占 warning 的绝大多数，它是「公式/图表密集页值得抽查」的提示，不代表一定有缺陷。

## 4. 严格变好判据

质量循环的核心是**不接受更差的候选**。评分与接受规则（`backend/quality.py`）：

1. **评分**：`score = (error_count, issue_count)`，按字典序比较；`(0, 8)` 优于 `(0, 9)`，`(0, 99)` 优于 `(1, 0)`。
2. **候选生成**：根据 issue 分类选择 `repair_layout` 或 `retranslate`，生成候选 PDF 与候选 `qa.json`。
3. **接受条件**：`candidate_score < best_score`（严格小于）才接受；相等也回滚——避免「修了但没变好」的震荡。
4. **回滚**：每轮修复前保存快照 `output/<job_id>/.snapshots/round-<n>/{translated.pdf,qa.json}`；候选不被接受时恢复快照，`pass_history[].rolled_back = true`。
5. **停止条件**：达到模式允许的轮数上限、没有可执行的修复动作、或候选连续不改善。

`pass_history` 记录了每次决策的完整证据：

```json
{
  "pass": 2,
  "issue_count": 9,
  "error_count": 0,
  "warning_count": 9,
  "agent_action": "repair_layout",
  "agent_reason": "caption_overlap on page 4",
  "issue_fingerprint": "sha1:…",
  "accepted": false,
  "rolled_back": true,
  "candidate_score": [0, 11]
}
```

## 5. `qa.json` schema（v1）

文件位置：`%APPDATA%\paperex\output\<job_id>\qa.json`；API：`GET /api/jobs/{id}/qa`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | int | 当前为 `1` |
| `job_id` | str | 任务 ID |
| `generated_at` | ISO-8601 | 生成时间 |
| `qa_mode` | str | `off / fast / standard / strict` |
| `status` | str | `passed / warning / failed / qa_failed` |
| `passes_run` | int | 实际 QA 次数 |
| `repair_attempted` | bool | 是否执行过修复 |
| `engine` | object | `{name, version, revision}` |
| `config_fingerprint` | str | 引擎/术语/QA 配置指纹（用于复现） |
| `score` | `[int, int]` | 最终 `[error_count, issue_count]` |
| `best_score` | `[int, int]` | 历史最优评分 |
| `pass_history` | array | 每轮决策记录（见上） |
| `issue_count` / `error_count` / `warning_count` | int | 汇总计数 |
| `issues` | array | `{page, code, message, severity, action}`；最多 500 条；`action ∈ accept / repair_layout / retranslate / stop` |
| `issues_truncated` | bool | 是否截断 |
| `terminology` | object | `{candidates_added, violations, snapshot_fingerprint, conflict_count}` |
| `cache` | object | `{hits, misses, fallback_blocks}` |
| `timings_ms` | object | `{translate, qa, repair, total}` |
| `checker_errors` | array | QA 子进程或检测器自身异常；有值时通常 `status=qa_failed` |
| `quality_mode` / `report_url` | str | 本任务实际生效的质量模式；报告 API 地址（`/api/jobs/{id}/qa`） |

说明：

- `status` 语义：`passed`＝无 issue；`warning`＝仅 warning；`failed`＝存在 error（strict gate 未通过）；`qa_failed`＝QA 进程失败（产物仍可用）。
- `issues[].message` 截断到 500 字符；`page` 为 1-based 页码。
- `score` 与 `best_score` 可用于跨版本对比同一篇论文的回归情况。
- 旧版本（v1.0）只有 `result.qa = {errors, warnings, total, codes, issues}`；v1.1 的 `qa.json` 是超集（字段名为 `qa_mode`，不是 `mode`）。任务详情的 `result.qa` 继续保留 `{errors, warnings, total, codes, issues}` 兼容形状供旧前端读取；`qa_summary` 为 `{errors,warnings,total,status,report_url}`。

示例（节选）：

```json
{
  "schema_version": 1,
  "job_id": "3f6c9a2e1b7d4c58",
  "generated_at": "2026-11-10T12:34:56+00:00",
  "qa_mode": "standard",
  "status": "warning",
  "passes_run": 2,
  "repair_attempted": true,
  "score": [0, 8],
  "best_score": [0, 8],
  "issue_count": 8,
  "error_count": 0,
  "warning_count": 8,
  "issues": [
    {
      "page": 3,
      "code": "high_risk_layout",
      "message": "Page 3: dense formula/table region detected",
      "severity": "warning",
      "action": "accept"
    }
  ],
  "timings_ms": {"translate": 90000, "qa": 35000, "repair": 0, "total": 125000}
}
```

## 6. API 错误码（非 QA issue）

任务级错误在 `GET /api/jobs/{id}` 中为：`error`（中文消息字符串）+ `error_code` + `retryable` + `error_detail = {code, message, retryable, hint}`；HTTP 错误响应则统一为 `{detail, code, message, hint, retryable, error:{...}}`。`message` 中文、`hint` 给出可执行建议：

| code | 触发场景 | retryable | 典型 hint |
| --- | --- | --- | --- |
| `auth_failed` | 401/403，Key 无效或无权限 | 否 | 检查设置页 API Key 与模型权限 |
| `provider_unreachable` | DNS/连接失败/超时 | 是 | 检查网络、base_url 与代理 |
| `provider_error` | 5xx、限流、响应格式错误 | 是 | 稍后重试或更换模型 |
| `invalid_pdf` | 文件损坏或不是 PDF | 否 | 确认文件完整 |
| `encrypted_pdf` | 加密/需要密码 | 否 | 先解除加密再翻译 |
| `ocr_required` | 无文本层且 OCR 不可用 | 否 | 安装 Tesseract 并设置 `PAPEREX_TESSDATA` |
| `ocr_unavailable` | 显式要求 OCR 但引擎不可用 | 否 | 安装 Tesseract 或关闭 OCR |
| `too_large` | 超过 120 MB 或 40 万字符 | 否 | 拆分 PDF |
| `no_text_layer` | 检测不到可翻译文本 | 否 | 扫描件先 OCR |
| `canceled` | 用户取消 | — | 可重新发起 |
| `not_found` | 任务/历史记录不存在（或已删除） | 否 | 刷新任务或历史列表 |
| `invalid_request` | 参数非法（quality_mode、max_attempts 等越界） | 否 | 按提示修正请求参数 |
| `interrupted` | 后端中断（心跳超时/进程退出）后恢复失败 | 是 | 重新发起任务 |
| `engine_error` | 引擎内部异常 | 视情况 | 附日志/`qa.json` 反馈 |
| `qa_failed` | QA 进程失败（通常不使任务失败） | 是 | 可忽略或降低质量模式 |
| `internal` | 未分类异常 | 视情况 | 附日志反馈 |

## 7. 降级与调参

| 变量 / 参数 | 作用 | 建议 |
| --- | --- | --- |
| `quality_mode=off` | 完全跳过 QA | 批量粗翻；产不出 `qa.json` |
| `quality_mode=fast` | QA 只报告 | 时间敏感但想看问题时 |
| `PAPEREX_QA_MAX_PASSES` | QA 总次数上限（1–8，默认 4） | 降低可缩短 strict 时间 |
| `PAPEREX_QA_STRICT_CODES` | 逗号分隔的 code 列表，降级为非阻断 | 只对已确认的历史误报使用；变更会进入 `config_fingerprint` |
| `PAPEREX_QA_MODE` | 默认质量模式（优先级最高） | 等价于设置页的默认质量模式 |
| `PAPEREX_POST_QA` | 兼容旧开关：`0`/`false`/`off` 等价于 `off`（仅在未设置 `PAPEREX_QA_MODE` 时生效） | 旧部署迁移用 |
| `PAPEREX_QA_STRICT_CODES` + `quality_mode=strict` | 让 strict 在存在已知误报时仍可通过 | 在 CI/批量里慎用，避免掩盖真实问题 |

> 降级不是修复。对反复出现的误报，应带 `qa.json` + 复现 PDF 提 issue（见 `SECURITY.md` 之外的普通 issue 模板），由引擎侧修检测器。

## 8. 性能

- QA 需要在渲染后的页面上做像素比较，是**计算密集型**步骤；11 页论文单次 QA 基线约为 verify 21.8 s + inspect 13.2 s（修复前测量，开发机）。v1.1 为 `verify/inspect` 增加了 `pages` 子集参数（缺陷 D7，0-based）；引擎已具备分片能力，但当前 `qa_worker.py` / 质量循环复检仍整篇运行，接入「只复检受影响页」留待后续版本。
- 超大论文（50+ 页）单次 QA 可能达到数分钟；`strict` 最多 4 次 QA 会显著拉长总耗时。时间敏感时用 `standard` 或 `fast`。
- QA 子进程默认超时 600 s（`qa.DEFAULT_TIMEOUT_S`）；此外质量循环整体有时间预算（`qa_loop.DEFAULT_TIME_BUDGET_S = 1800 s`）。超时记为 `qa_failed`，不阻塞任务完成。
- 翻译缓存命中可显著减少模型调用（`qa.json.cache.hits` 可观察），但**不减少 QA 时间**。

## 9. 测试

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests/test_quality.py -q        # 纯逻辑：分类/评分/规划/strict gate
.venv\Scripts\python.exe -m pytest tests/test_qa_worker.py -q      # 子进程协议、超时、崩溃降级
.venv\Scripts\python.exe -m pytest tests/test_engine_regression.py -q  # QA 检测器回归（离线 fixture，不调用 LLM）
.venv\Scripts\python.exe -m pytest tests -q                        # 全量
```

离线引擎自测（无需 API Key）：

```bash
.venv\Scripts\python.exe tests\test_engine_offline.py
```

相关文档：[ARCHITECTURE.md](ARCHITECTURE.md)（质量循环在整体架构中的位置）、[DEVELOPMENT.md](DEVELOPMENT.md)（测试与调试）、[ROADMAP.md](ROADMAP.md)（QA 后续改进方向）。
