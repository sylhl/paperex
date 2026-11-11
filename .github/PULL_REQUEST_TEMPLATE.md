# Pull Request

## 变更说明

<!-- 这个 PR 做了什么？为什么需要它？关联 issue：Closes #123 -->

## 类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 文档
- [ ] 测试 / 构建 / CI
- [ ] 重构（不改变行为）
- [ ] 其他（请说明）

## 验证方式

<!-- 贴出实际执行过的命令与结果，而不是「已测试」 -->

```powershell
# 后端
cd paperex\backend
.\.venv\Scripts\python.exe -m pytest tests -q
ruff check .

# 前端
cd ..\frontend
npm run build
```

## 兼容性影响

- [ ] 不影响 API / 缓存 / 产物格式
- [ ] 影响 API（字段/语义变化，已更新 README 与 CHANGELOG）
- [ ] 影响缓存键或产物目录（已说明迁移方式）
- [ ] 影响打包或依赖（已更新 NOTICE / requirements）

## 检查清单

- [ ] 已补充或更新测试，且不依赖真实 API Key / 外网
- [ ] 行为变更已同步 `README.md` / `docs/` / `CHANGELOG.md`（Unreleased）
- [ ] 未提交 `dist/`、`.venv/`、`target/`、`launcher/*.exe` 或论文原文
- [ ] 未包含任何真实 API Key、密钥或未公开论文内容
- [ ] 新增第三方资源已更新 `NOTICE` 并保留其许可证文件
- [ ] 提交信息清晰（推荐 Conventional Commits；可选 `git commit -s` 表示 DCO）

## 截图 / 补充

<!-- UI 变更请附前后对比截图；无则不填 -->
