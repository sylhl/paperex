# -*- coding: utf-8 -*-
"""文件型任务存储与状态机（plan §3）。

每个任务的持久化文件是 ``<output>/<job_id>/job.json``：原子写（tmp + os.replace），
内存里保存一份热数据供进度回调高频更新；``flush()`` 限频落盘，终态强制落盘。
进度回调可能来自翻译线程，因此所有读写都走 ``threading.RLock``。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

JOB_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

JOB_STATUSES = ("queued", "running", "done", "error", "cancelling", "cancelled")
TERMINAL_STATUSES = ("done", "error", "cancelled")
ACTIVE_STATUSES = ("queued", "running", "cancelling")

STAGES = (
    "queued",
    "analyzing",
    "translating",
    "qa",
    "repairing",
    "writing",
    "done",
    "failed",
    "canceled",
)
STAGE_LABELS = {
    "queued": "排队中",
    "analyzing": "解析 PDF",
    "translating": "翻译中",
    "qa": "质量检查",
    "repairing": "修复中",
    "writing": "写入结果",
    "done": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}

STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "done": "已完成",
    "error": "失败",
    "cancelling": "正在取消",
    "cancelled": "已取消",
}

STAGE_FOR_TERMINAL = {"done": "done", "error": "failed", "cancelled": "canceled"}

FLUSH_INTERVAL = 2.0
STALE_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3

_SECRET_KEYS = {"api_key", "apikey", "authorization", "token", "api_token", "secret"}


class JobError(RuntimeError):
    """应用层结构化错误；``engine_adapter.classify_error`` 会优先识别这些属性。"""

    code = "internal"
    hint = ""
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        hint: Optional[str] = None,
        retryable: Optional[bool] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if hint is not None:
            self.hint = hint
        if retryable is not None:
            self.retryable = retryable

    def as_detail(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


class JobCanceled(JobError):
    code = "canceled"
    hint = "任务已取消，未生成半成品文件"
    retryable = False

    def __init__(self, message: str = "任务已取消") -> None:
        super().__init__(message)


class JobNotFound(JobError):
    code = "not_found"
    hint = "任务可能已过期或被删除"
    retryable = False

    def __init__(self, message: str = "任务不存在或已过期") -> None:
        super().__init__(message)


def max_attempts_default() -> int:
    raw = (os.environ.get("PAPEREX_MAX_ATTEMPTS") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        value = DEFAULT_MAX_ATTEMPTS
    return max(1, min(8, value))


def resume_enabled() -> bool:
    raw = (os.environ.get("PAPEREX_RESUME_JOBS") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _now() -> float:
    return time.time()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_secrets(item) for item in value]
    return value


def stage_for_status(status: str, stage: Optional[str] = None) -> str:
    if status in STAGE_FOR_TERMINAL:
        return STAGE_FOR_TERMINAL[status]
    return stage if stage in STAGES else "queued"


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def can_cancel(status: str) -> bool:
    return status in ("queued", "running")


class JobStore:
    """线程安全的 job.json 仓库。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._last_flush: Dict[str, float] = {}

    # ------------------------------------------------------------------ 基础
    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def job_file(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def create(
        self,
        job_id: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        source_path: Optional[Path] = None,
        status: str = "queued",
        stage: str = "queued",
        message: str = "任务已创建，等待开始",
        attempt: int = 0,
    ) -> Dict[str, Any]:
        now = _now()
        job: Dict[str, Any] = {
            "schema_version": 1,
            "job_id": job_id,
            "status": status,
            "stage": stage,
            "progress": 0.0,
            "message": message,
            "created_at": now,
            "updated_at": now,
            "heartbeat_at": now,
            "attempt": int(attempt),
            "params": _strip_secrets(_json_safe(params or {})),
            "source_path": str(source_path) if source_path else None,
            "pages": {"total": 0, "done": 0},
            "quality": {"mode": None, "round": None},
            "timings": {"translate": None, "qa": None, "repair": None, "total": None},
            "result": None,
            "error": None,
            "error_detail": None,
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._cancel_events.setdefault(job_id, threading.Event())
            self._flush_unlocked(job_id, force=True)
        return copy.deepcopy(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not job_id:
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return copy.deepcopy(job)
        loaded = self._load_from_disk(job_id)
        if loaded is not None:
            with self._lock:
                self._jobs.setdefault(job_id, loaded)
                return copy.deepcopy(self._jobs[job_id])
        return None

    def exists(self, job_id: str) -> bool:
        return self.get(job_id) is not None

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        for path in self._iter_job_files():
            job_id = path.parent.name
            job = self.get(job_id)
            if job is not None:
                jobs.append(job)
        jobs.sort(key=lambda item: float(item.get("created_at") or 0.0), reverse=True)
        return jobs

    _CRITICAL_FIELDS = frozenset(
        {"status", "stage", "result", "error", "error_detail", "attempt", "message", "params", "quality"}
    )

    def update(self, job_id: str, *, force: bool = False, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                loaded = self._load_from_disk(job_id)
                if loaded is None:
                    raise JobNotFound()
                self._jobs[job_id] = loaded
                job = loaded
            for key, value in fields.items():
                job[key] = _json_safe(value)
            if "status" in fields and "stage" not in fields:
                job["stage"] = stage_for_status(str(job["status"]), job.get("stage"))
            job["updated_at"] = _now()
            if "heartbeat_at" not in fields:
                job["heartbeat_at"] = job["updated_at"]
            if not force and fields.keys() & self._CRITICAL_FIELDS:
                force = True  # 状态/阶段等关键字段变更立即落盘
            self._flush_unlocked(job_id, force=force)
            return copy.deepcopy(job)

    def heartbeat(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                job[key] = _json_safe(value)
            job["heartbeat_at"] = _now()
            job["updated_at"] = job["heartbeat_at"]
            self._flush_unlocked(job_id, force=False)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._last_flush.pop(job_id, None)
        path = self.job_file(job_id)
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ 取消
    def cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            event = self._cancel_events.get(job_id)
            if event is None:
                event = threading.Event()
                self._cancel_events[job_id] = event
            return event

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(job_id)
            return bool(event and event.is_set())

    def request_cancel(self, job_id: str) -> Optional[Dict[str, Any]]:
        """设置取消 Event 并把状态推进到 cancelling；终态任务返回 None。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                loaded = self._load_from_disk(job_id)
                if loaded is None:
                    return None
                self._jobs[job_id] = loaded
                job = loaded
            status = str(job.get("status") or "")
            if is_terminal(status):
                return None
            event = self._cancel_events.setdefault(job_id, threading.Event())
            event.set()
            job["cancel_requested"] = True
            if status != "cancelling":
                job["status"] = "cancelling"
                job["message"] = "正在取消任务…"
            job["updated_at"] = _now()
            self._flush_unlocked(job_id, force=True)
            return copy.deepcopy(job)

    # ------------------------------------------------------------------ 恢复
    def recover(
        self,
        *,
        resume: Optional[bool] = None,
        stale_seconds: float = STALE_SECONDS,
        engine_revision: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> Dict[str, List[str]]:
        """启动时扫描磁盘任务：中断任务重排队或标失败，已请求取消的标取消。

        返回 ``{requeue, interrupted, canceled}``（job_id 列表）。
        """
        resume = resume_enabled() if resume is None else bool(resume)
        attempts = max_attempts_default() if max_attempts is None else max(1, int(max_attempts))
        result: Dict[str, List[str]] = {"requeue": [], "interrupted": [], "canceled": []}
        now = _now()
        for path in self._iter_job_files():
            job_id = path.parent.name
            job = self.get(job_id)
            if job is None:
                continue
            status = str(job.get("status") or "")
            if is_terminal(status):
                continue
            updated_at = float(job.get("updated_at") or job.get("created_at") or 0.0)
            if now - updated_at < stale_seconds:
                continue
            if job.get("cancel_requested"):
                self.update(
                    job_id,
                    force=True,
                    status="cancelled",
                    stage="canceled",
                    message="任务已取消",
                    error="任务已取消",
                    error_detail={"code": "canceled", "message": "任务已取消", "hint": "", "retryable": False},
                )
                result["canceled"].append(job_id)
                continue
            if resume and status in ("queued", "running") and int(job.get("attempt") or 0) < attempts:
                revision_changed = bool(
                    engine_revision
                    and job.get("params", {}).get("engine_revision")
                    and job["params"]["engine_revision"] != engine_revision
                )
                attempt = 0 if revision_changed else int(job.get("attempt") or 0) + 1
                self.update(
                    job_id,
                    force=True,
                    status="queued",
                    stage="queued",
                    progress=0.0,
                    attempt=attempt,
                    message="服务重启后自动重新排队（已完成翻译会复用缓存）",
                    started_at=None,
                )
                result["requeue"].append(job_id)
                continue
            self.update(
                job_id,
                force=True,
                status="error",
                stage="failed",
                message="任务因服务重启中断",
                error="任务因服务重启中断，可重新提交（已完成的翻译会复用缓存）",
                error_detail={
                    "code": "interrupted",
                    "message": "任务因服务重启中断",
                    "hint": "重新提交任务即可；已完成的翻译会复用全局缓存",
                    "retryable": True,
                },
            )
            result["interrupted"].append(job_id)
        return result

    def queue_position(self, job_id: str) -> int:
        """排队位置：运行中/终态返回 0，排队中返回 1 起的序号。"""
        job = self.get(job_id)
        if job is None or job.get("status") != "queued":
            return 0
        queued = [
            item
            for item in self.list_jobs()
            if item.get("status") == "queued"
        ]
        queued.sort(key=lambda item: float(item.get("created_at") or 0.0))
        for index, item in enumerate(queued):
            if item.get("job_id") == job_id:
                return index + 1
        return 0

    # ------------------------------------------------------------------ 内部
    def _iter_job_files(self) -> List[Path]:
        if not self.root.is_dir():
            return []
        return sorted(self.root.glob("*/job.json"))

    def _load_from_disk(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self.job_file(job_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("job_id") != job_id:
            return None
        return payload

    def _flush_unlocked(self, job_id: str, *, force: bool) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        now = _now()
        last = self._last_flush.get(job_id, 0.0)
        critical = force or is_terminal(str(job.get("status") or ""))
        if not critical and now - last < FLUSH_INTERVAL:
            return
        directory = self.job_dir(job_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "job.json"
            tmp = directory / "job.json.tmp"
            tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
            self._last_flush[job_id] = now
        except OSError:
            # 落盘失败不应打断翻译主流程；内存状态仍然可用
            pass

__all__ = [
    "ACTIVE_STATUSES",
    "DEFAULT_MAX_ATTEMPTS",
    "JOB_ID_RE",
    "JOB_STATUSES",
    "STAGES",
    "STAGE_LABELS",
    "STATUS_LABELS",
    "TERMINAL_STATUSES",
    "JobCanceled",
    "JobError",
    "JobNotFound",
    "JobStore",
    "can_cancel",
    "is_terminal",
    "max_attempts_default",
    "resume_enabled",
    "stage_for_status",
]
