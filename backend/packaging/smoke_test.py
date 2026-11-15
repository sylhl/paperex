# -*- coding: utf-8 -*-
"""End-to-end smoke test for the frozen backend bundle.

Starts ``paperex-server.exe`` from a clean temporary ``APPDATA`` in dry-run
mode, translates a generated two-page paper (no network / no API key needed),
and checks that the isolated QA worker also runs inside the frozen app.

Usage::

    python backend/packaging/smoke_test.py
    python backend/packaging/smoke_test.py --exe path/to/paperex-server.exe --keep
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pymupdf

PACKAGING_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGING_DIR.parent
DEFAULT_EXE = BACKEND_DIR / "dist" / "paperex-server" / "paperex-server.exe"
TEXT_LINES = [
    "PaperEx frozen backend smoke test",
    "This synthetic two page document exercises the in place reflow pipeline.",
    "The backend must start without a system Python installation.",
    "Translation uses the deterministic dry run supplier in this test.",
    "Formula and figure regions are preserved while body text is replaced.",
]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_paper(path: Path) -> None:
    document = pymupdf.open()
    for page_index in range(2):
        page = document.new_page(width=595, height=842)
        y = 90.0
        page.insert_text((72, y), TEXT_LINES[0] if page_index == 0 else "Second page body", fontsize=18)
        y += 40
        for line in TEXT_LINES:
            page.insert_text((72, y), line, fontsize=11)
            y += 22
            if y > 760:
                break
        page.insert_text((72, 800), f"page {page_index + 1}/2", fontsize=9)
    document.save(path)
    document.close()


def wait_health(client: httpx.Client, deadline: float, process: subprocess.Popen | None = None) -> dict:
    last_error = ""
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"backend exited with code {process.returncode} before becoming healthy "
                f"(last connection error: {last_error})"
            )
        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as error:  # server still booting
            last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"backend did not become healthy within the deadline: {last_error}")


def run_flow(client: httpx.Client, pdf: Path, timeout_s: float) -> dict:
    create = client.post(
        "/api/jobs",
        json={
            "pdf_path": str(pdf),
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "dry-run",
            "quality_mode": "fast",
        },
    )
    if create.status_code != 200:
        raise RuntimeError(
            f"POST /api/jobs returned HTTP {create.status_code}: {create.text[:2000]}"
        )
    job_id = create.json()["job_id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        status = job.get("status")
        if status in {"done", "failed", "cancelled"}:
            return job
        time.sleep(1.0)
    raise SystemExit(f"job {job_id} did not finish within {timeout_s}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="smoke test the frozen paperex backend")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--timeout", type=float, default=240.0, help="job timeout in seconds")
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=240.0,
        help="seconds to wait for the frozen backend to answer /api/health",
    )
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    args = parser.parse_args()

    exe = args.exe.resolve()
    if not exe.is_file():
        print(f"frozen backend not found: {exe}", file=sys.stderr)
        print("build it first: python backend/packaging/build_backend.py", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="paperex-smoke-"))
    appdata = root / "appdata"
    appdata.mkdir()
    pdf = root / "smoke.pdf"
    make_paper(pdf)

    env = dict(os.environ)
    env["APPDATA"] = str(appdata)
    env["PAPEREX_DRYRUN"] = "1"
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("PAPEREX_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    port = free_port()
    log_path = root / "server.log"
    log = log_path.open("w", encoding="utf-8", errors="replace")
    started = time.monotonic()
    process = subprocess.Popen(
        [str(exe), "--port", str(port)],
        cwd=str(root),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    failures: list[str] = []
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=30.0) as client:
            health = wait_health(client, time.monotonic() + args.health_timeout, process)
            boot_s = time.monotonic() - started
            print(f"health ready in {boot_s:.1f}s: {json.dumps(health, ensure_ascii=False)[:200]}")

            if health.get("ok") is not True:
                failures.append("health.ok is not true")
            version = str(health.get("version", ""))
            if not version.startswith("1."):
                failures.append(f"unexpected version {version!r}")
            if not health.get("dry_run"):
                failures.append("health.dry_run is not true")
            if not health.get("engine"):
                failures.append("health.engine is empty")
            terminology = health.get("terminology")
            if not isinstance(terminology, int) or terminology < 1000:
                failures.append(f"terminology corpus missing: {terminology!r}")

            job = run_flow(client, pdf, args.timeout)
            job_id = job.get("job_id", "")
            print(f"job {job_id}: status={job.get('status')} progress={job.get('progress')}")
            if job.get("status") != "done":
                failures.append(f"job status {job.get('status')!r}: {job.get('error')}")
            result = job.get("result") or {}
            for field in (
                "original_url",
                "translated_url",
                "pages",
                "translated_blocks",
                "skipped_blocks",
                "elapsed_ms",
            ):
                if field not in result:
                    failures.append(f"result missing {field}")
            translated_url = result.get("translated_url")
            if translated_url:
                download = client.get(translated_url)
                if download.status_code != 200 or not download.content.startswith(b"%PDF"):
                    failures.append("translated PDF download failed")
                else:
                    print(f"translated PDF: {len(download.content) / 1024:.0f} KB")

            qa_response = client.get(f"/api/jobs/{job_id}/qa")
            if qa_response.status_code != 200:
                failures.append(f"qa endpoint HTTP {qa_response.status_code}")
            else:
                qa = qa_response.json()
                print(f"qa: status={qa.get('status')} errors={qa.get('errors')} warnings={qa.get('warnings')}")
                if not isinstance(qa.get("errors"), int):
                    failures.append("qa.errors is not an int (isolated QA worker did not run)")
                if qa.get("status") not in {"passed", "warning", "done", None}:
                    failures.append(f"unexpected qa status {qa.get('status')!r}")

            history = client.get("/api/history").json()
            history_items = history if isinstance(history, list) else history.get("items", [])
            if not any(item.get("job_id") == job_id for item in history_items if isinstance(item, dict)):
                failures.append("history does not contain the job")

        qa_file = appdata / "paperex" / "output" / job_id / "qa.json"
        if not qa_file.is_file():
            failures.append(f"qa.json not written: {qa_file}")
        else:
            report = json.loads(qa_file.read_text(encoding="utf-8"))
            print(f"qa.json schema={report.get('schema_version')} mode={report.get('mode')}")

        elapsed = time.monotonic() - started
        print(f"elapsed: {elapsed:.1f}s")
    except RuntimeError as error:
        failures.append(str(error))
    except Exception as error:  # noqa: BLE001 - report any client/flow failure with server logs
        import traceback  # noqa: PLC0415

        traceback.print_exc()
        failures.append(f"{type(error).__name__}: {error}")
    finally:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        if failures:
            print(f"--- server exit code: {process.returncode} ---", file=sys.stderr)
            print("--- server log tail ---", file=sys.stderr)
            print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)

    if args.keep:
        print(f"artifacts kept at {root}")
    else:
        import shutil  # noqa: PLC0415

        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("SMOKE FAILED:", file=sys.stderr)
        for item in failures:
            print(f" - {item}", file=sys.stderr)
        return 1
    print("SMOKE OK · frozen backend translated a PDF and ran the isolated QA worker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
