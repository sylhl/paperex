# -*- coding: utf-8 -*-
"""paperex 后端 pytest 公共配置与 fixture。

职责：
1. **数据隔离**：在导入 `main` 之前把 `%APPDATA%` 指向临时目录，避免测试
   写入用户真实的 ``%APPDATA%/paperex``（历史记录 / 输出 / 缓存）。
2. **绝不调用真实翻译 API**：默认 ``PAPEREX_DRYRUN=1``（EchoTranslator），
   并清除进程内的 ``*_API_KEY`` 环境变量。
3. 提供 ``client``（FastAPI TestClient）、``sample_pdf``（离线样例 PDF）、
   ``wait_job``（轮询任务到终态）等共享 fixture / 工具。

运行：
    cd backend && .venv/Scripts/python.exe -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
for _path in (BACKEND_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# ---------------------------------------------------------------------------
# 必须在任何测试模块 import main 之前完成环境隔离
# ---------------------------------------------------------------------------
_SESSION_APPDATA = Path(tempfile.mkdtemp(prefix="paperex-pytest-appdata-"))
os.environ["APPDATA"] = str(_SESSION_APPDATA)
os.environ["PAPEREX_DRYRUN"] = "1"
os.environ["PAPEREX_POST_QA"] = "0"
for _key_env in (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "PAPEREX_API_KEY",
    "PAPERREFLOW_API_KEY",
):
    os.environ.pop(_key_env, None)

#: 测试进程私有沙盒根目录（``_SESSION_APPDATA/paperex``）。
TEST_APPDATA_ROOT = _SESSION_APPDATA / "paperex"


def pytest_configure(config: pytest.Config) -> None:
    """启动时打印沙盒路径，方便失败时排查残留产物。"""
    config.addinivalue_line("markers", "slow: 较慢的回归测试（真实引擎整篇 QA），默认跳过")
    config.addinivalue_line("markers", "e2e: 需要真实服务/网络的端到端测试，默认跳过")
    print(f"\n[paperex-tests] APPDATA 隔离目录 = {_SESSION_APPDATA}")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="运行 @pytest.mark.slow 标记的慢测试（整篇 PDF 的视觉 QA 回归）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="慢测试默认跳过；加 --run-slow 运行")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """测试全绿时清理临时沙盒；有失败则保留现场便于排查。"""
    if exitstatus == 0:
        shutil.rmtree(_SESSION_APPDATA, ignore_errors=True)


# ---------------------------------------------------------------------------
# 引擎 / 应用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_module():
    """导入 FastAPI 应用模块（此时 APPDATA 已完成隔离）。"""
    import main

    return main


@pytest.fixture()
def client(app_module) -> Any:
    """FastAPI TestClient；每个用例独立 lifespan，服务端状态互不残留。"""
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """离线样例 PDF（两页：标题/摘要/双栏正文/公式/图表/参考文献）。"""
    from test_engine_offline import build_sample_pdf

    path = tmp_path / "sample.pdf"
    build_sample_pdf(path)
    return path


@pytest.fixture()
def offline_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """确保当前用例走 Echo 离线流水线（不访问真实 LLM）。"""
    monkeypatch.setenv("PAPEREX_DRYRUN", "1")
    monkeypatch.setenv("PAPEREX_POST_QA", "0")


# ---------------------------------------------------------------------------
# 轮询 / 断言工具（被多个测试模块复用）
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"done", "failed", "error", "canceled", "cancelled", "qa_failed"}


def wait_job(
    client: Any,
    job_id: str,
    *,
    timeout: float = 180.0,
    interval: float = 0.5,
    terminal: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """轮询 GET /api/jobs/{id} 直到终态，返回最后一次 job detail。"""
    terminal = terminal or TERMINAL_STATUSES
    deadline = time.time() + timeout
    detail: Dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail.get("status") in terminal:
            return detail
        time.sleep(interval)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未到终态，最后状态={detail}")


def wait_until_ready(base_url: str, timeout: float = 90.0) -> Dict[str, Any]:
    """等待真实/子进程 uvicorn 的 /api/health 就绪，返回 health JSON。"""
    import httpx

    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            response = httpx.get(base_url + "/api/health", timeout=2.0, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except Exception as exc:  # noqa: BLE001 - 服务未就绪时继续等待
            last_error = exc
        time.sleep(0.3)
    raise AssertionError(f"服务 {base_url} 在 {timeout}s 内未就绪：{last_error}")


def assert_job_done(client: Any, job_id: str, *, timeout: float = 180.0) -> Dict[str, Any]:
    """轮询并要求任务成功完成，返回 job detail。"""
    detail = wait_job(client, job_id, timeout=timeout)
    assert detail.get("status") == "done", json.dumps(detail, ensure_ascii=False, indent=2)
    return detail


#: 供其他模块 ``from conftest import ...`` 使用（pytest 会把 tests/ 加入 sys.path）。
__all__ = [
    "TEST_APPDATA_ROOT",
    "TERMINAL_STATUSES",
    "assert_job_done",
    "wait_job",
    "wait_until_ready",
]
