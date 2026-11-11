# -*- coding: utf-8 -*-
"""主进程 QA 编排（plan §4）：并发跑隔离子进程、合并去重、写 qa.json。

设计要点：

- **隔离**：``qa_worker.py`` 是独立进程，checker 崩溃不会拖垮主进程；
- **并发**：每个 checker 一个子进程，同时启动；
- **降级**：子进程超时/崩溃只记 ``qa_failed``，job 仍然成功（错误进
  ``checker_errors`` 与 warning 列表）；
- **schema**：报告字段对齐 port-spec 附录 B，供 ``GET /api/jobs/{id}/qa``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import quality

QA_SCHEMA_VERSION = 1
DEFAULT_CHECKERS = ("verify", "inspect")
DEFAULT_TIMEOUT_S = 600.0
MAX_ISSUES_IN_REPORT = 500


def qa_timeout_s() -> float:
    raw = (os.environ.get("PAPEREX_QA_TIMEOUT") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        value = DEFAULT_TIMEOUT_S
    return max(30.0, min(1800.0, value))


def worker_path() -> Path:
    return Path(__file__).resolve().parent / "qa_worker.py"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class QaResult:
    """一次 QA 的结论（主进程使用）。"""

    issues: List[Dict[str, Any]]
    status: str
    score: Tuple[int, int]
    fingerprint: str
    timings_ms: Dict[str, int] = field(default_factory=dict)
    checkers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    qa_failed: bool = False
    error: Optional[Dict[str, Any]] = None
    checker_errors: List[Dict[str, Any]] = field(default_factory=list)
    passes_run: int = 1

    @property
    def errors(self) -> int:
        return int(self.score[0])

    @property
    def warnings(self) -> int:
        return int(self.score[1]) - int(self.score[0])

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "score": list(self.score),
            "fingerprint": self.fingerprint,
            "issues": self.issues,
            "timings_ms": dict(self.timings_ms),
            "checkers": dict(self.checkers),
            "qa_failed": self.qa_failed,
            "error": self.error,
        }


def status_for(issues: Sequence[Dict[str, Any]], *, qa_failed: bool = False, mode: str = "standard") -> str:
    if mode == "off":
        return "skipped"
    if qa_failed:
        return "qa_failed"
    errors, total = quality.score(list(issues))
    if errors:
        return "failed"
    if total - errors:
        return "warning"
    return "passed"


def _read_result_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def run_checkers(
    original_pdf: Path,
    translated_pdf: Path,
    *,
    checkers: Sequence[str] = DEFAULT_CHECKERS,
    max_pages: Optional[int] = None,
    timeout: Optional[float] = None,
    scratch_dir: Optional[Path] = None,
    python_exe: Optional[str] = None,
) -> Dict[str, Any]:
    """并发启动 checker 子进程，返回合并结果（不抛异常）。"""
    original_pdf = Path(original_pdf)
    translated_pdf = Path(translated_pdf)
    timeout = qa_timeout_s() if timeout is None else max(1.0, float(timeout))
    python_exe = python_exe or sys.executable
    scratch = Path(scratch_dir) if scratch_dir else None
    if scratch is not None:
        scratch.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    results: Dict[str, Any] = {}
    processes: List[Tuple[str, subprocess.Popen]] = []
    specs: Dict[str, Path] = {}

    for checker in checkers:
        result_path = (scratch / f"qa-{checker}-{token}.json") if scratch else None
        if result_path is None:
            import tempfile

            result_path = Path(tempfile.mkdtemp(prefix="paperex-qa-")) / f"qa-{checker}-{token}.json"
        spec = {
            "original_pdf": str(original_pdf),
            "translated_pdf": str(translated_pdf),
            "checkers": [checker],
            "max_pages": int(max_pages) if max_pages else None,
            "result_path": str(result_path),
        }
        spec_path = result_path.with_suffix(".spec.json")
        try:
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            results[checker] = {
                "ok": False,
                "issues": [],
                "error": {"code": "qa_failed", "message": f"无法写入 QA spec：{exc}"},
            }
            continue
        specs[checker] = result_path
        try:
            process = subprocess.Popen(
                [python_exe, str(worker_path()), str(spec_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent),
            )
        except OSError as exc:  # pragma: no cover - 无法启动进程
            results[checker] = {
                "ok": False,
                "issues": [],
                "error": {"code": "qa_failed", "message": f"无法启动 QA 子进程：{exc}"},
            }
            continue
        processes.append((checker, process))

    deadline = time.monotonic() + timeout
    for checker, process in processes:
        remaining = max(0.5, deadline - time.monotonic())
        reason: Optional[str] = None
        try:
            _stdout, stderr = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况
                pass
            reason = f"QA 子进程超时（>{timeout:.0f}s）"
            stderr = b""
        if reason is not None:
            results[checker] = {
                "ok": False,
                "issues": [],
                "error": {
                    "code": "qa_failed",
                    "message": reason,
                    "hint": "可以减少页数或调大 PAPEREX_QA_TIMEOUT",
                },
            }
            continue
        payload = _read_result_file(Path(specs[checker]))
        if payload is None:
            detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
            tail = detail[-1][:300] if detail else ""
            results[checker] = {
                "ok": False,
                "issues": [],
                "error": {
                    "code": "qa_failed",
                    "message": f"QA 子进程未产出结果（exit={process.returncode}）"
                    + (f"：{tail}" if tail else ""),
                },
            }
            continue
        payload.setdefault("issues", [])
        payload.setdefault("checkers", {})
        results[checker] = payload

    all_issues: List[Dict[str, Any]] = []
    checker_reports: Dict[str, Dict[str, Any]] = {}
    checker_errors: List[Dict[str, Any]] = []
    failed = 0
    for checker in checkers:
        payload = results.get(checker) or {
            "ok": False,
            "issues": [],
            "error": {"code": "qa_failed", "message": "没有 QA 结果"},
        }
        if not payload.get("ok"):
            failed += 1
            error = payload.get("error") or {}
            checker_errors.append(
                {
                    "checker": checker,
                    "code": str(error.get("code") or "qa_failed"),
                    "message": str(error.get("message") or "QA 检查失败"),
                }
            )
        all_issues.extend(payload.get("issues") or [])
        nested = payload.get("checkers") or {}
        info = nested.get(checker) if len(nested) == 1 else None
        checker_reports[checker] = {
            "ok": bool(payload.get("ok")),
            "issue_count": len(payload.get("issues") or []),
            "elapsed_ms": int((info or {}).get("elapsed_ms") or payload.get("elapsed_ms") or 0),
            "error": payload.get("error"),
        }

    merged = quality.merge_issues(all_issues)
    qa_failed = bool(checkers) and failed == len(checkers)
    return {
        "issues": merged,
        "checkers": checker_reports,
        "checker_errors": checker_errors,
        "qa_failed": qa_failed,
        "error": checker_errors[0] if checker_errors else None,
        "timings_ms": int((time.perf_counter() - started) * 1000),
    }


def evaluate(
    original_pdf: Path,
    translated_pdf: Path,
    *,
    mode: str = "standard",
    max_pages: Optional[int] = None,
    timeout: Optional[float] = None,
    scratch_dir: Optional[Path] = None,
    runner: Optional[Callable[..., Dict[str, Any]]] = None,
) -> QaResult:
    """跑一次隔离 QA 并给出规整结论；永不抛异常（qa_failed 也返回）。"""
    runner = run_checkers if runner is None else runner
    raw = runner(
        Path(original_pdf),
        Path(translated_pdf),
        checkers=DEFAULT_CHECKERS,
        max_pages=max_pages,
        timeout=timeout,
        scratch_dir=scratch_dir,
    )
    issues = quality.annotate_issues(quality.merge_issues(raw.get("issues") or []))
    qa_failed = bool(raw.get("qa_failed"))
    status = status_for(issues, qa_failed=qa_failed, mode=mode)
    score = quality.score(issues)
    return QaResult(
        issues=issues,
        status=status,
        score=score,
        fingerprint=quality.issue_fingerprint(issues),
        timings_ms={"qa": int(raw.get("timings_ms") or 0)},
        checkers=dict(raw.get("checkers") or {}),
        qa_failed=qa_failed,
        error=raw.get("error"),
        checker_errors=[dict(item) for item in (raw.get("checker_errors") or [])],
    )


def build_report(
    *,
    job_id: str,
    mode: str,
    result: QaResult,
    passes_run: int = 1,
    repair_attempted: bool = False,
    pass_history: Optional[List[Dict[str, Any]]] = None,
    engine: Optional[Dict[str, Any]] = None,
    config_fingerprint: str = "",
    terminology: Optional[Dict[str, Any]] = None,
    cache_stats: Optional[Dict[str, Any]] = None,
    timings_ms: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装 qa.json（port-spec 附录 B schema）。"""
    if engine is None:
        try:
            from engine_adapter import engine_info

            engine = engine_info()
        except Exception:  # pragma: no cover - 引擎异常时兜底
            engine = {"name": "pdf_zh_translator", "version": "unknown", "revision": "unknown"}
    issues = list(result.issues)
    truncated = len(issues) > MAX_ISSUES_IN_REPORT
    errors, total = quality.score(issues)
    warnings = max(0, int(total) - int(errors))
    history = list(pass_history or [])
    if not history:
        history = [
            {
                "pass": 0,
                "issue_count": len(issues),
                "error_count": errors,
                "warning_count": warnings,
                "agent_action": "baseline",
                "agent_reason": "初始翻译结果",
                "issue_fingerprint": result.fingerprint,
                "accepted": True,
            }
        ]
    best_score = list(result.score)
    for entry in reversed(history):
        score = entry.get("candidate_score") or entry.get("score")
        if entry.get("accepted") and isinstance(score, (list, tuple)) and len(score) == 2:
            best_score = list(score)
            break
    report: Dict[str, Any] = {
        "schema_version": QA_SCHEMA_VERSION,
        "job_id": job_id,
        "generated_at": _utc_now_iso(),
        "qa_mode": mode,
        "status": result.status,
        "passes_run": int(passes_run),
        "repair_attempted": bool(repair_attempted),
        "engine": dict(engine),
        "config_fingerprint": config_fingerprint,
        "score": [int(result.score[0]), int(result.score[1])],
        "best_score": [int(best_score[0]), int(best_score[1])],
        "pass_history": history,
        "issue_count": len(issues),
        "error_count": errors,
        "warning_count": warnings,
        "issues": [
            {
                "page": issue.get("page"),
                "code": issue.get("code"),
                "severity": issue.get("severity"),
                "message": str(issue.get("message") or "")[:500],
                "action": issue.get("action") or quality.issue_action(issue.get("code"), issue.get("severity")),
            }
            for issue in issues[:MAX_ISSUES_IN_REPORT]
        ],
        "issues_truncated": truncated,
        "terminology": dict(
            terminology
            or {
                "candidates_added": 0,
                "violations": 0,
                "snapshot_fingerprint": "",
                "conflict_count": 0,
            }
        ),
        "cache": dict(cache_stats or {"hits": 0, "misses": 0, "fallback_blocks": 0}),
        "timings_ms": dict(
            timings_ms or {"translate": 0, "qa": int(result.timings_ms.get("qa") or 0), "repair": 0, "total": 0}
        ),
        "checker_errors": [dict(item) for item in result.checker_errors],
    }
    return report


def write_report(path: Path, report: Dict[str, Any]) -> Path:
    """原子写 qa.json。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def summarize(report: Dict[str, Any]) -> Dict[str, Any]:
    """API 里的 ``qa_summary``。"""
    return {
        "errors": int(report.get("error_count") or 0),
        "warnings": int(report.get("warning_count") or 0),
        "total": int(report.get("issue_count") or 0),
        "status": report.get("status"),
        "report_url": report.get("report_url"),
    }


def load_report(path: Path) -> Optional[Dict[str, Any]]:
    if not Path(path).is_file():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "DEFAULT_CHECKERS",
    "QA_SCHEMA_VERSION",
    "QaResult",
    "build_report",
    "evaluate",
    "load_report",
    "qa_timeout_s",
    "run_checkers",
    "status_for",
    "summarize",
    "worker_path",
    "write_report",
]
