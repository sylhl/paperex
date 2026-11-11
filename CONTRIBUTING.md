# 贡献指南

感谢你愿意改进 paperex。无论是报告缺陷、改进文档、补充测试还是提交代码，都很有价值。

## 行为准则

- 尊重不同背景与经验水平的贡献者；对事不对人。
- 技术讨论以可复现的证据为准：附版本、复现步骤、日志或 `qa.json` 摘要。
- 不发布他人论文内容、未公开稿件或 API Key；示例请脱敏。

## 开始之前

- 使用/安装问题先看 [README](README.md) 与 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
- 较大的功能或行为变更，请先开 issue 说明动机、方案与影响面，避免大 PR 返工。
- 安全漏洞**不要**开公开 issue，见 [SECURITY.md](SECURITY.md)。

## 开发流程

1. Fork 仓库并创建分支：
   - `feat/<topic>`：新功能
   - `fix/<topic>`：缺陷修复
   - `docs/<topic>`、`test/<topic>`、`chore/<topic>`
2. 按 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 准备环境（后端 venv + 前端 `npm install`）。
3. 修改代码并补测试；行为变更同步更新 README、`docs/`、`CHANGELOG.md`（在 `Unreleased` 下）。
4. 本地跑完质量门禁（见下）。
5. 提交并推送，按 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md) 发起 Pull Request。

## 测试要求

提交前至少通过：

```powershell
# 后端
cd paperex\backend
.\.venv\Scripts\python.exe -m pytest tests -q
ruff check .

# 前端
cd ..\frontend
npm run build
```

约定：

- 新功能必须有对应测试；缺陷修复尽量补一个能复现旧问题的回归测试。
- 测试**不得**依赖真实 API Key、外网或真实模型调用；使用 `PAPEREX_DRYRUN=1`、fixture 或 mock。
- 需要真实服务的用例标记 `slow` / `e2e`，默认跳过（CI 不执行）。
- 引擎行为测试优先使用 `backend/tests/fixtures/` 的固定 PDF，保证可重复。

## 代码风格

### 后端（Python）

- `ruff check backend` 必须通过；不要用 `# noqa` 掩盖问题，确有必要的需在 PR 中说明。
- 使用类型注解；公共函数写简短中文 docstring；避免在路由层堆业务逻辑。
- 质量逻辑（分类、评分、修复规划）保持在 `quality.py` 的纯函数中，便于单测；不要在其中做 I/O。
- 不随意增加依赖；新增运行时依赖需说明理由、许可证（同步 `NOTICE`）与打包影响。

### 前端（JavaScript / React）

- 函数组件 + hooks；状态与 API 封装放 `src/lib/`、`src/hooks/`，避免继续膨胀单文件。
- 不引入新的运行时依赖（当前仅 React、react-pdf/pdfjs-dist、Tauri API）。
- UI 文案使用中文；交互需可用键盘操作，重要控件提供 `aria-label`。
- 主题颜色走 `src/constants.js` 的 THEMES，禁止硬编码新色值。
- 样式沿用 Tailwind 实用类；复杂样式放 `src/index.css` 并有注释。

### 文档

- 中文为主，命令以 Windows PowerShell 为主、跨平台另注。
- 路径使用仓库内真实相对路径；命令必须实际可运行。
- 不写未实现的能力；不确定的内容标注「待验证」或放入「已知限制」。

## 提交信息

推荐 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(qa): 支持 strict 模式下按页复检
fix(engine): 修复公式原子被截断 (D4)
docs(readme): 更新实测数据与已知限制
test(cache): 增加缓存分桶回归用例
```

- 标题 ≤ 72 字符，使用祈使语气；正文说明「为什么」而不是罗列文件。
- 一个提交只做一件事；不要提交生成物（`dist/`、`.venv/`、`target/`、`launcher/*.exe`）。

## Pull Request

- 保持 PR 聚焦、可review；大改动拆分或先开 issue 讨论。
- 描述中写清：动机、方案、验证方式（命令与结果）、兼容性影响（API/缓存/产物格式）。
- 关联 issue（`Closes #123`）。
- 维护者可能要求补充测试或调整文案；请及时响应。
- 合并前需通过 CI。

## 开发者原创声明（DCO，可选但推荐）

在提交信息中加 `Signed-off-by` 表示你有权提交该代码并同意以本项目许可证发布：

```bash
git commit -s -m "feat(qa): ..."
```

签名格式：`Signed-off-by: 你的名字 <you@example.com>`。若你所在组织对贡献有额外要求，请在 PR 中说明。

## 许可证

- 本项目以 **AGPL-3.0** 发布（见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)）。
- 提交代码即表示你同意以 AGPL-3.0 授权你的贡献，并确认你有权这样做。
- 不要提交来自不兼容许可证的代码、字体、图片或论文原文；引入第三方资源必须同时更新 `NOTICE` 并保留其许可证文件。

## 发布流程（维护者）

1. 更新版本号：`backend/main.py` 的 `APP_VERSION`、`frontend/package.json`、`frontend/src-tauri/tauri.conf.json`。
2. 把 `CHANGELOG.md` 的 `Unreleased` 改为版本小节并写日期。
3. 更新 README 实测数据、截图与已知限制。
4. 跑全量测试与构建；打 tag（`vX.Y.Z`）；整理 Release notes（引用 CHANGELOG）。
5. 确认 LICENSE / NOTICE / 字体 OFL 文件完整，`new/`、`_work/` 未入库。
