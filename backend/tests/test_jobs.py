# -*- coding: utf-8 -*-
"""jobs.JobStore / jobs 状态机 / backend.ocr 结构错误测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import jobs
from jobs import JobCanceled, JobError, JobNotFound, JobStore


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "output")


def _make_job(store: JobStore, job_id: str = "a1b2c3d4", **kwargs):
    params = {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash", "api_key": "sk-secret"}
    params.update(kwargs.pop("params", {}))
    return store.create(job_id, params=params, source_path=Path("C:/tmp/source.pdf"), **kwargs)


def test_create_get_roundtrip(store: JobStore):
    job = _make_job(store)
    assert job["job_id"] == "a1b2c3d4"
    assert job["status"] == "queued"
    assert job["stage"] == "queued"
    assert job["progress"] == 0.0
    assert job["params"]["model"] == "deepseek-flash"
    assert "api_key" not in job["params"], "API Key 绝不能落盘"
    assert json.loads((store.job_dir("a1b2c3d4") / "job.json").read_text(encoding="utf-8"))["job_id"] == "a1b2c3d4"
    assert not (store.job_dir("a1b2c3d4") / "job.json.tmp").exists()
    assert store.get("a1b2c3d4")["source_path"].endswith("source.pdf")


def test_get_unknown_returns_none(store: JobStore):
    assert store.get("deadbeef") is None
    assert store.get("") is None
    assert not store.exists("deadbeef")


def test_update_sets_stage_and_timestamps(store: JobStore):
    _make_job(store)
    before = store.get("a1b2c3d4")["updated_at"]
    time.sleep(0.01)
    job = store.update("a1b2c3d4", status="running", message="翻译中", progress=0.42)
    assert job["status"] == "running"
    assert job["stage"] == "queued"  # 未显式给 stage 时保持当前阶段
    assert job["progress"] == 0.42
    assert job["updated_at"] > before
    assert store.get("a1b2c3d4")["message"] == "翻译中"


def test_update_terminal_sets_stage(store: JobStore):
    _make_job(store)
    assert store.update("a1b2c3d4", status="done")["stage"] == "done"
    _make_job(store, "b1b2c3d4")
    assert store.update("b1b2c3d4", status="error")["stage"] == "failed"
    _make_job(store, "c1b2c3d4")
    assert store.update("c1b2c3d4", status="cancelled")["stage"] == "canceled"


def test_update_unknown_raises(store: JobStore):
    with pytest.raises(JobNotFound):
        store.update("deadbeef", status="running")


def test_heartbeat(store: JobStore):
    _make_job(store)
    before = store.get("a1b2c3d4")["heartbeat_at"]
    time.sleep(0.01)
    store.heartbeat("a1b2c3d4", pages={"total": 10, "done": 3})
    job = store.get("a1b2c3d4")
    assert job["heartbeat_at"] > before
    assert job["pages"] == {"total": 10, "done": 3}


def test_cancel_sets_event_and_cancelling(store: JobStore):
    _make_job(store)
    store.update("a1b2c3d4", status="running", stage="translating")
    cancelled = store.request_cancel("a1b2c3d4")
    assert cancelled is not None
    assert cancelled["status"] == "cancelling"
    assert cancelled["cancel_requested"] is True
    assert store.is_cancelled("a1b2c3d4")
    assert store.cancel_event("a1b2c3d4").is_set()


def test_cancel_terminal_returns_none(store: JobStore):
    _make_job(store)
    store.update("a1b2c3d4", status="done")
    assert store.request_cancel("a1b2c3d4") is None


def test_cancel_unknown_returns_none(store: JobStore):
    assert store.request_cancel("deadbeef") is None


def test_queue_position(store: JobStore):
    _make_job(store, "a0000001")
    time.sleep(0.005)
    _make_job(store, "a0000002")
    time.sleep(0.005)
    _make_job(store, "a0000003")
    assert store.queue_position("a0000001") == 1
    assert store.queue_position("a0000003") == 3
    store.update("a0000001", status="running")
    assert store.queue_position("a0000001") == 0
    assert store.queue_position("a0000002") == 1


def test_persistence_across_store_instances(tmp_path: Path):
    root = tmp_path / "output"
    first = JobStore(root)
    first.create("a1b2c3d4", params={"model": "m"})
    first.update("a1b2c3d4", status="running", progress=0.5)
    second = JobStore(root)
    job = second.get("a1b2c3d4")
    assert job is not None
    assert job["status"] == "running"
    assert job["progress"] == 0.5


def test_delete(store: JobStore):
    _make_job(store)
    assert store.delete("a1b2c3d4")
    assert store.get("a1b2c3d4") is None
    assert not store.job_file("a1b2c3d4").exists()
    assert not store.delete("deadbeef")


def test_recover_requeues_stale_running(store: JobStore, monkeypatch):
    monkeypatch.setenv("PAPEREX_RESUME_JOBS", "1")
    _make_job(store)
    store.update("a1b2c3d4", status="running", stage="translating", progress=0.3)
    store.update("a1b2c3d4", attempt=0, force=True)
    # 把 updated_at 拨旧，模拟进程崩溃
    job = store.get("a1b2c3d4")
    job["updated_at"] = time.time() - 3600
    store._jobs["a1b2c3d4"] = job  # noqa: SLF001 - 测试直接构造陈旧状态
    result = store.recover(resume=True, stale_seconds=60)
    assert "a1b2c3d4" in result["requeue"]
    recovered = store.get("a1b2c3d4")
    assert recovered["status"] == "queued"
    assert recovered["stage"] == "queued"
    assert recovered["attempt"] == 1


def test_recover_without_resume_marks_interrupted(store: JobStore):
    _make_job(store)
    store.update("a1b2c3d4", status="running")
    store._jobs["a1b2c3d4"]["updated_at"] = time.time() - 3600  # noqa: SLF001
    result = store.recover(resume=False, stale_seconds=60)
    assert result["interrupted"] == ["a1b2c3d4"]
    job = store.get("a1b2c3d4")
    assert job["status"] == "error"
    assert job["error_detail"]["code"] == "interrupted"
    assert job["error_detail"]["retryable"] is True


def test_recover_cancel_requested_marks_cancelled(store: JobStore):
    _make_job(store)
    store.update("a1b2c3d4", status="cancelling")
    store._jobs["a1b2c3d4"]["cancel_requested"] = True  # noqa: SLF001
    store._jobs["a1b2c3d4"]["updated_at"] = time.time() - 3600  # noqa: SLF001
    result = store.recover(resume=True, stale_seconds=60)
    assert result["canceled"] == ["a1b2c3d4"]
    assert store.get("a1b2c3d4")["status"] == "cancelled"


def test_recover_skips_fresh_and_terminal(store: JobStore):
    _make_job(store)
    store.update("a1b2c3d4", status="done")
    _make_job(store, "b1b2c3d4")
    result = store.recover(resume=True, stale_seconds=60)
    assert result == {"requeue": [], "interrupted": [], "canceled": []}


def test_recover_engine_revision_change_resets_attempt(store: JobStore):
    _make_job(store, params={"engine_revision": "old"})
    store.update("a1b2c3d4", status="queued", attempt=2, force=True)
    store._jobs["a1b2c3d4"]["updated_at"] = time.time() - 3600  # noqa: SLF001
    result = store.recover(resume=True, stale_seconds=60, engine_revision="new", max_attempts=3)
    assert "a1b2c3d4" in result["requeue"]
    assert store.get("a1b2c3d4")["attempt"] == 0


def test_max_attempts_env(monkeypatch):
    monkeypatch.setenv("PAPEREX_MAX_ATTEMPTS", "9")
    assert jobs.max_attempts_default() == 8
    monkeypatch.setenv("PAPEREX_MAX_ATTEMPTS", "0")
    assert jobs.max_attempts_default() == 1
    monkeypatch.setenv("PAPEREX_MAX_ATTEMPTS", "bad")
    assert jobs.max_attempts_default() == 3


def test_resume_enabled_env(monkeypatch):
    monkeypatch.delenv("PAPEREX_RESUME_JOBS", raising=False)
    assert jobs.resume_enabled() is True
    monkeypatch.setenv("PAPEREX_RESUME_JOBS", "0")
    assert jobs.resume_enabled() is False


def test_job_errors_as_detail():
    error = JobError("出错了", code="provider_error", hint="稍后重试", retryable=True)
    assert error.as_detail() == {
        "code": "provider_error",
        "message": "出错了",
        "hint": "稍后重试",
        "retryable": True,
    }
    assert JobCanceled().code == "canceled"
    assert JobCanceled().retryable is False


def test_stage_helpers():
    assert jobs.stage_for_status("error") == "failed"
    assert jobs.stage_for_status("cancelled") == "canceled"
    assert jobs.stage_for_status("running", "qa") == "qa"
    assert jobs.stage_for_status("running", "bogus") == "queued"
    assert jobs.can_cancel("queued") and jobs.can_cancel("running")
    assert not jobs.can_cancel("cancelling")
    assert jobs.is_terminal("done") and jobs.is_terminal("error") and jobs.is_terminal("cancelled")
    assert not jobs.is_terminal("queued")


# --------------------------------------------------------------------------- OCR


def _blank_pdf(path: Path, pages: int = 2) -> Path:
    import fitz

    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    document.save(str(path))
    document.close()
    return path


def _text_pdf(path: Path) -> Path:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Hello world, this is a translatable paragraph for testing purposes.")
    document.save(str(path))
    document.close()
    return path


def test_ocr_analyze_blank_pdf_needs_ocr(tmp_path: Path):
    import ocr

    info = ocr.analyze(_blank_pdf(tmp_path / "blank.pdf"))
    assert info["pages"] == 2
    assert info["text_pages"] == 0
    assert info["needs_ocr"] is True


def test_ocr_analyze_text_pdf(tmp_path: Path):
    import ocr

    info = ocr.analyze(_text_pdf(tmp_path / "text.pdf"))
    assert info["text_pages"] == 1
    assert info["needs_ocr"] is False


def test_ocr_mode_env(monkeypatch):
    import ocr

    monkeypatch.delenv("PAPEREX_OCR", raising=False)
    assert ocr.ocr_mode() == "auto"
    monkeypatch.setenv("PAPEREX_OCR", "FORCE")
    assert ocr.ocr_mode() == "force"
    monkeypatch.setenv("PAPEREX_OCR", "bogus")
    assert ocr.ocr_mode() == "auto"


def test_ocr_status_shape():
    import ocr

    payload = ocr.status()
    assert set(payload) >= {"available", "engine", "hint", "mode"}
    assert isinstance(payload["available"], bool)


def test_ocr_off_rejects_scanned_pdf(tmp_path: Path, monkeypatch):
    import ocr

    monkeypatch.setenv("PAPEREX_OCR", "off")
    with pytest.raises(ocr.OcrError) as info:
        ocr.preprocess_if_needed(_blank_pdf(tmp_path / "blank.pdf"), tmp_path)
    assert info.value.code == "no_text_layer"
    assert info.value.hint


def test_ocr_auto_without_tesseract_raises_ocr_unavailable(tmp_path: Path, monkeypatch):
    import ocr

    monkeypatch.setenv("PAPEREX_OCR", "auto")
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    with pytest.raises(ocr.OcrError) as info:
        ocr.preprocess_if_needed(_blank_pdf(tmp_path / "blank.pdf"), tmp_path)
    assert info.value.code == "ocr_unavailable"
    assert "Tesseract" in info.value.hint


def test_ocr_auto_text_pdf_passthrough(tmp_path: Path, monkeypatch):
    import ocr

    monkeypatch.setenv("PAPEREX_OCR", "auto")
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    pdf = _text_pdf(tmp_path / "text.pdf")
    result = ocr.preprocess_if_needed(pdf, tmp_path)
    assert result["used"] is False
    assert Path(result["path"]) == pdf


def test_ocr_describe_failure():
    import ocr

    detail = ocr.describe_failure(ocr.OcrError("ocr_unavailable", "缺 Tesseract", hint="安装"))
    assert detail == {"code": "ocr_unavailable", "message": "缺 Tesseract", "hint": "安装", "retryable": False}
    assert ocr.describe_failure(ValueError("x"))["code"] == "internal"
