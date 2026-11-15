# 发布与打包（RELEASING）

paperex 的 Windows 安装包内置了**冻结后的本地后端**：用户下载安装后不需要
Python、Node 或任何命令行操作，双击即用。本文档面向维护者，说明本地打包、
版本号更新与 GitHub Release 自动化。

---

## 1. 产物结构

安装包（NSIS，Release 资产名为 `paperex-v1.2.0-windows-x64-setup.exe`）安装后的目录：

```
%LOCALAPPDATA%\paperex\            # 当前用户安装（installMode=currentUser）
├─ paperex.exe                     # Tauri 桌面主程序（Rust）
└─ backend\                        # 随包分发的冻结后端（PyInstaller onedir）
   ├─ paperex-server.exe           # FastAPI 服务 + `--qa-worker` 隔离子进程入口
   └─ _internal\                   # Python 运行时、PyMuPDF(MuPDF)、字体、术语库
```

* 启动时 `frontend/src-tauri/src/lib.rs` 先在源码树中找 `backend/main.py`（开发模式），
  找不到再执行 `%LOCALAPPDATA%\paperex\backend\paperex-server.exe`；
* 端口固定 `127.0.0.1:8756`，退出时 shell 会 kill 后端子进程；
* 运行期数据只写入 `%APPDATA%\paperex`（缓存、历史、术语候选、字体），不写安装目录。

启动器构建产物 `launcher/paperex.exe` 属于**另一条路径**（便携的 dev 启动器，
不含内置后端），不随 Release 分发，见 `launcher/README.md`。

---

## 2. 本地构建安装包

前置：Windows 10/11 x64、Python 3.12–3.13、Node ≥ 20、Rust ≥ 1.77.2、
Visual Studio Build Tools（C++ 工作负载）、Node 依赖已装。

```powershell
cd paperex

# 1) 后端依赖与 PyInstaller
python -m venv backend/.venv
backend\.venv\Scripts\python -m pip install -r backend/requirements.txt pyinstaller

# 2) 冻结后端 -> backend/dist/paperex-server/
backend\.venv\Scripts\python backend\packaging\build_backend.py

# 3) 冒烟测试（离线 dry-run 翻译 + 隔离子进程 QA）
backend\.venv\Scripts\python backend\packaging\smoke_test.py

# 4) 前端依赖 + 打包（自动验证 resources 是否就位）
cd frontend
npm ci
npm run tauri build
```

产物：`frontend/src-tauri/target/release/bundle/nsis/paperex_<version>_x64-setup.exe`
（bundler 的原始文件名；发布时由工作流统一重命名为 `paperex-v<version>-windows-x64-setup.exe`）。

构建脚本会自动：

* 把开发树里的引擎指纹写入 `backend/packaging/_build/engine_revision.txt`，
  冻结运行时 `engine_adapter.engine_fingerprint()` 直接读取——保证安装版与
  源码树使用同一个缓存 bucket / qa.json revision；
* 生成 Windows 版本资源（ProductVersion / FileVersion）；
* 将 `backend/dist/paperex-server/` 映射为安装包资源目录下的 `backend/`。

> 首次 `tauri build` 会联网下载 NSIS 工具链，属正常现象。

---

## 3. 本地验证安装包

```powershell
# 静默安装到临时目录（/S 静默，/D 指定目录且必须是最后一个参数）
$tmp = "$env:TEMP\paperex-install-test"
Start-Process .\paperex-v1.2.0-windows-x64-setup.exe -ArgumentList "/S", "/D=$tmp" -Wait
Get-ChildItem $tmp
Test-Path "$tmp\backend\paperex-server.exe"   # 应为 True

# 直接跑安装目录里的后端，确认冻结运行时可用
& "$tmp\backend\paperex-server.exe" --port 8790
# 浏览器访问 http://127.0.0.1:8790/api/health 应返回 version=1.2.0

# 验证完删除
& "$tmp\uninstall.exe" /S
```

安装包未做代码签名（`signCommand` 未配置），Windows SmartScreen 可能提示
「Windows 已保护你的电脑」，选择「更多信息 → 仍要运行」即可。正式分发前的
可选改进：购买代码签名证书并配置 `bundle.windows.signCommand`。

---

## 4. 版本号更新清单

发布新版本时同步以下位置（全部为 `1.2.0` 这类 `MAJOR.MINOR.PATCH`）：

| 文件 | 字段 |
| --- | --- |
| `backend/main.py` | `APP_VERSION` |
| `frontend/package.json` | `version` |
| `frontend/src-tauri/tauri.conf.json` | `version` |
| `frontend/src-tauri/Cargo.toml` | `version` |
| `README.md` | 顶部版本徽章 `version-<x.y.z>` |
| `CHANGELOG.md` | 新增 `## [x.y.z] - YYYY-MM-DD` |

`frontend/package-lock.json` 与 `Cargo.lock` 会在 `npm install` / `cargo build`
后自动更新，一并提交。升级后端依赖或引擎文件后，`engine_revision` 会在下次
`build_backend.py` 时自动重算，旧翻译缓存随之失效（设计如此）。

---

## 5. GitHub Release 自动化

`.github/workflows/release.yml` 在推送 `v*` 标签时于 `windows-latest` 上执行：

1. 建 venv、安装后端依赖与 PyInstaller；
2. `build_backend.py` 冻结后端；
3. `smoke_test.py` 冒烟测试（内置 dry-run 翻译 + QA 子进程）；
4. `npm ci` + `npm run tauri build` 生成 NSIS 安装包；
5. 收集安装包，统一重命名为 `paperex-v<version>-windows-x64-setup.exe`，上传工作流 artifact；
6. `softprops/action-gh-release` 创建/更新 Release（标题 `paperex v<version>`）并附带安装包资产。

发布流程：

```powershell
git add -A
git commit -m "release: paperex v1.2.0"
git tag -a v1.2.0 -m "paperex v1.2.0"
git push origin main
git push origin v1.2.0
```

推送后打开 GitHub → Actions → Release，等待 green；Release 页面会自动附带
安装包（唯一资产）与 GitHub 原生的 SHA-256 展示。命名约定：

* Release 标题：`paperex v<version>`
* 安装包资产：`paperex-v<version>-windows-x64-setup.exe`
* 不再额外上传 `SHA256SUMS.txt` 之类文件——Release 页面每个资产右侧即显示
  GitHub 计算的 `sha256:`，需要校验时复制该值即可。

`workflow_dispatch` 手动触发只产出 artifact、不建 Release，便于先在 PR 上验证打包链路。

---

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| `tauri build` 报 resources 路径不存在 | 先跑 `build_backend.py`，确认 `backend/dist/paperex-server/paperex-server.exe` 存在 |
| 安装后打开显示「后端离线」 | 手动运行 `<安装目录>\backend\paperex-server.exe --port 8756` 观察报错；确认杀软未隔离该 exe |
| 冻结后端启动即退出 | 用 `--port` 前台运行看 traceback；常见为缺数据文件（corpus/layout_families/字体）——检查 spec 的 `datas` |
| QA 报 `qa_open_failed` | 冻结入口需支持 `--qa-worker`；确认 `qa.py` 在冻结分支使用 `[sys.executable, "--qa-worker", spec]` |
| 安装包体积过大（~50 MB） | 主要来自 MuPDF 与 CJK 字体；可用 UPX/字体裁剪优化，但需重新验证渲染 |
