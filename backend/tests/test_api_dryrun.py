# -*- coding: utf-8 -*-
"""真实子进程 HTTP 链路自测（离线 dry-run，不需要 API Key）。

用当前解释器启动 ``uvicorn main:app``（``PAPEREX_DRYRUN=1``、独立临时
``APPDATA``），然后走完整链路：

    GET /api/health -> POST /api/jobs -> 轮询 GET /api/jobs/{id}
    -> 校验 result 兼容字段 -> GET /api/files/{translated_url} 返回 PDF
    -> GET /api/history 能看到任务

pytest 运行（测试自己负责起停服务，不依赖外部已启动的 uvicorn）::

    cd backend && .venv/Scripts/python.exe -m pytest tests/test_api_dryrun.py -q

直接运行（同样自起自停）::

    cd backend && .venv/Scripts/python.exe tests/test_api_dryrun.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import httpx
import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

#: 向后兼容旧脚本：可用 argv 覆盖服务地址 / PDF 路径（仅 __main__ 模式）。
DEFAULT_BASE = "http://127.0.0.1:8791"
REQUIRED_RESULT_FIELDS = (
    "original_url",
    "translated_url",
    "output_path",
    "pages",
    "blocks",
    "translated_blocks",
    "skipped_blocks",
    "failed_blocks",
    "font",
    "elapsed_ms",
    "size_bytes",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ensure_sample(path: Path) -> Path:
    """样例 PDF 由 test_engine_offline 生成；缺失时现场造一份，保证测试自包含。"""
    if path.is_file():
        return path
    from test_engine_offline import build_sample_pdf  # 延迟导入，避免拖慢收集

    path.parent.mkdir(parents=True, exist_ok=True)
    build_sample_pdf(path)
    return path


def _wait_health(base_url: str, timeout: float = 90.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            response = httpx.get(base_url + "/api/health", timeout=2.0, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except Exception as exc:  # noqa: BLE001 - 服务尚未就绪，继续等
            last_error = exc
        time.sleep(0.3)
    raise AssertionError(f"uvicorn 在 {timeout}s 内未就绪：{last_error}")


class UvicornServer:
    """以子进程启动真实 uvicorn，退出时确保进程被回收。"""

    def __init__(
        self,
        *,
        port: Optional[int] = None,
        appdata: Optional[Path] = None,
        log_path: Optional[Path] = None,
    ) -> None:
        self.port = port or _free_port()
        self.appdata = appdata
        self.log_path = log_path
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: Optional[subprocess.Popen] = None
        self._log_file = None

    def __enter__(self) -> "UvicornServer":
        env = os.environ.copy()
        env["PAPEREX_DRYRUN"] = "1"
        env["PAPEREX_POST_QA"] = "0"
        if self.appdata is not None:
            env["APPDATA"] = str(self.appdata)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
            stdout: Any = self._log_file
        else:
            stdout = subprocess.DEVNULL
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_health(self.base_url)
        except Exception:
            self.stop()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


def run_http_flow(base_url: str, pdf_path: Path, *, timeout: float = 300.0) -> Dict[str, Any]:
    """旧版契约链路：health -> 创建任务 -> 轮询 -> 结果字段 -> 文件下载 -> 历史。"""
    client = httpx.Client(timeout=30.0, trust_env=False)
    try:
        health = client.get(base_url + "/api/health").json()
        for key in ("ok", "version", "engine", "font", "terminology"):
            assert key in health, f"health 缺少字段 {key}"
        assert health["ok"] is True, health

        created = client.post(
            base_url + "/api/jobs",
            json={
                "pdf_path": str(pdf_path),
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
            },
        )
        assert created.status_code == 200, created.text
        created = created.json()
        job_id = created.get("job_id")
        assert job_id, f"创建任务响应缺少 job_id：{created}"

        deadline = time.time() + timeout
        job: Dict[str, Any] = {}
        while time.time() < deadline:
            response = client.get(f"{base_url}/api/jobs/{job_id}")
            assert response.status_code == 200, response.text
            job = response.json()
            if job.get("status") in {"done", "error"}:
                break
            time.sleep(1.0)
        assert job.get("status") == "done", f"任务未成功：{job}"

        result = job["result"]
        missing = [key for key in REQUIRED_RESULT_FIELDS if key not in result]
        assert not missing, f"缺少兼容字段：{missing}"
        assert Path(result["output_path"]).is_file(), "输出 PDF 不存在"
        assert result["pages"] >= 2, result
        assert result["translated_blocks"] > 0, result
        assert isinstance(result.get("qa"), dict), f"缺少 qa 摘要：{result.get('qa')}"

        response = client.get(base_url + result["translated_url"])
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"%PDF"), "translated_url 返回的不是 PDF"

        history = client.get(base_url + "/api/history").json()
        assert any(item.get("job_id") == job_id for item in history.get("items", [])), (
            "历史记录中找不到刚完成的任务"
        )
        return {"health": health, "job": job, "result": result, "history_total": history.get("total")}
    finally:
        client.close()


@pytest.fixture()
def dryrun_server(tmp_path: Path) -> Iterator[UvicornServer]:
    """子进程 uvicorn（dry-run + 独立 APPDATA），每个用例独占端口。"""
    with UvicornServer(
        appdata=tmp_path / "appdata",
        log_path=tmp_path / "uvicorn.log",
    ) as server:
        yield server


def test_dryrun_http_flow(dryrun_server: UvicornServer, tmp_path: Path) -> None:
    """真实 uvicorn 进程上的完整 HTTP 链路（离线）。"""
    pdf_path = ensure_sample(tmp_path / "fixtures" / "sample.pdf")
    summary = run_http_flow(dryrun_server.base_url, pdf_path)
    assert summary["health"]["dry_run"] is True, "测试必须运行在 dry-run 模式"
    assert summary["result"]["size_bytes"] > 0


def main() -> None:
    """直接运行：自起 uvicorn（默认随机端口），跑一遍链路后退出。"""
    base = DEFAULT_BASE if "--keep-base" in sys.argv else None
    if base is not None:
        pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else TESTS_DIR / "fixtures" / "sample.pdf"
        summary = run_http_flow(base, ensure_sample(pdf))
        print("OK ·", summary["result"]["pages"], "pages;", summary["result"]["elapsed_ms"], "ms")
        return
    import tempfile

    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        with UvicornServer(
            appdata=root / "appdata", log_path=root / "uvicorn.log"
        ) as server:
            summary = run_http_flow(
                server.base_url, ensure_sample(root / "fixtures" / "sample.pdf")
            )
        print("OK · 接口链路与字段兼容性通过")
        print("  pages:", summary["result"]["pages"])
        print("  blocks:", summary["result"]["blocks"], "translated:", summary["result"]["translated_blocks"])
        print("  output:", summary["result"]["translated_url"])
        print("  history_total:", summary["history_total"])


if __name__ == "__main__":
    main()
