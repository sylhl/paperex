# -*- coding: utf-8 -*-
"""paperex HTTP API 契约测试（plan §2 / engine-defects / port-spec 附录 B）。

分两层：

* **old-compat**：v1.0 已有端点与字段必须继续成立（前端/桌面端兼容）；
* **v1.1 契约**：质量循环、取消、持久化、缓存、QA、models、terminology 等。

v1.1 端点由 backend-dev 并行落盘：尚未注册的路由自动 skip（并给出提示），
落盘后自动收紧为硬断言。所有用例走 ``PAPEREX_DRYRUN=1`` 的 Echo 流水线，
不会访问真实翻译 API。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# conftest 已把 tests/ 加入 sys.path
from conftest import assert_job_done, wait_job  # noqa: E402

# 旧版 result 必须保留的字段（前端阅读器/历史依赖）
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

# v1.1 job detail 新增字段（plan §2）
V11_JOB_DETAIL_FIELDS = (
    "job_id",
    "status",
    "stage",
    "stage_label",
    "progress",
    "pages_done",
    "pages_total",
    "eta_seconds",
    "quality_round",
    "quality_mode",
    "can_cancel",
    "error",
    "qa_summary",
    "files",
    "timings_ms",
)

# plan §2 的 health.features 键
V11_HEALTH_FEATURES = (
    "isolated_qa",
    "quality_loop",
    "job_persistence",
    "cancel",
    "ocr",
    "global_cache",
    "terminology_api",
    "models_api",
)

# qa.json（port-spec 附录 B）核心字段
QA_REQUIRED_FIELDS = (
    "schema_version",
    "job_id",
    "generated_at",
    "qa_mode",
    "status",
    "passes_run",
    "engine",
    "issue_count",
    "error_count",
    "warning_count",
    "issues",
    "timings_ms",
)


def _get_json(client, path: str, *, method: str = "get", body: dict | None = None):
    """请求并返回 (status_code, json)。"""
    if method == "get":
        response = client.get(path)
    elif method == "delete":
        response = client.delete(path)
    elif body is None:
        response = client.post(path)
    else:
        response = client.post(path, json=body)
    try:
        payload = response.json()
    except ValueError:
        payload = {"_raw": response.text[:400]}
    return response.status_code, payload


def _health(client) -> dict:
    status, payload = _get_json(client, "/api/health")
    assert status == 200, payload
    return payload


def _route_paths(app_module) -> set[str]:
    return {getattr(route, "path", "") for route in app_module.app.routes}


def _require_route(app_module, path: str) -> None:
    if path not in _route_paths(app_module):
        pytest.skip(f"v1.1 端点 {path} 尚未落盘（backend-dev 并行改造中）")


def _is_v11(health: dict) -> bool:
    """v1.1 后端的标志：health.engine 从字符串升级为 {name,version,revision} 对象。"""
    return isinstance(health.get("engine"), dict)


def _require_v11(health: dict) -> None:
    if not _is_v11(health):
        pytest.skip("后端仍为 v1.0（health.engine 为字符串），等待 backend-dev 落盘 v1.1")


def _require_feature(health: dict, feature: str) -> None:
    features = health.get("features")
    if not isinstance(features, dict) or not features.get(feature):
        pytest.skip(f"v1.1 能力 features.{feature} 尚未落盘（backend-dev 并行改造中）")


def _error_payload(payload: dict) -> dict | None:
    """兼容 {error:{...}} 与 {detail:{error:{...}}} 两种错误封装。"""
    if isinstance(payload.get("error"), dict):
        return payload["error"]
    detail = payload.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    return None


def _create_job(client, pdf_path, **extra) -> str:
    body = {"pdf_path": str(pdf_path)}
    body.update(extra)
    status, payload = _get_json(client, "/api/jobs", method="post", body=body)
    assert status == 200, f"POST /api/jobs 失败：{status} {payload}"
    assert payload.get("job_id"), payload
    return str(payload["job_id"])


# ---------------------------------------------------------------------------
# 旧版兼容（v1.0 前端不能坏）
# ---------------------------------------------------------------------------


def test_health_legacy_fields(client) -> None:
    """v1.0 health 字段全部保留，且测试运行在 dry-run 隔离环境。"""
    health = _health(client)
    for field in (
        "ok",
        "app",
        "version",
        "appdata",
        "cache_dir",
        "output_dir",
        "font",
        "key_configured",
        "pymupdf",
        "dry_run",
    ):
        assert field in health, f"/api/health 缺少旧字段 {field}: {json.dumps(health, ensure_ascii=False)[:500]}"
    assert health["ok"] is True
    assert health["dry_run"] is True, "测试必须运行在 PAPEREX_DRYRUN=1，禁止访问真实翻译 API"


def test_job_minimal_flow_old_contract(client, sample_pdf) -> None:
    """上传→任务→轮询→产物→下载→历史：v1.0 最小链路端到端。

    同时是 v1.1 的兼容基线：新实现必须保留 ``result`` 内全部旧字段。
    """
    job_id = _create_job(client, sample_pdf)
    detail = assert_job_done(client, job_id)
    assert detail.get("job_id") == job_id
    assert detail.get("status") == "done"
    for field in ("progress", "message", "created_at"):
        assert field in detail, f"job detail 缺少旧字段 {field}"
    result = detail.get("result") or {}
    missing = [field for field in REQUIRED_RESULT_FIELDS if field not in result]
    assert not missing, f"result 缺少旧字段 {missing}: {json.dumps(result, ensure_ascii=False)[:500]}"

    original_status, original_payload = _get_json(client, result["original_url"])
    assert original_status == 200, original_payload
    translated_status, translated_payload = _get_json(client, result["translated_url"])
    assert translated_status == 200, translated_payload
    # FileResponse 在 TestClient 中会以 bytes 返回（json 解析失败无妨）
    assert Path(result["output_path"]).is_file(), "result.output_path 指向的产物不存在"

    status, history = _get_json(client, "/api/history")
    assert status == 200 and history.get("total", 0) >= 1, history
    job_ids = [item.get("job_id") for item in history.get("items", [])]
    assert job_id in job_ids, f"历史记录未包含刚完成的任务：{job_ids}"


def test_unknown_job_returns_404(client) -> None:
    status, payload = _get_json(client, "/api/jobs/nonexistent-job-id")
    assert status == 404, payload


def test_invalid_pdf_rejected(client, tmp_path) -> None:
    """不存在的 PDF 必须被拒绝（v1.0 为 400 detail，v1.1 应带结构化错误）。"""
    status, payload = _get_json(
        client, "/api/jobs", method="post", body={"pdf_path": str(tmp_path / "missing.pdf")}
    )
    assert status == 400, payload


# ---------------------------------------------------------------------------
# v1.1 health / models / terminology / cache
# ---------------------------------------------------------------------------


def test_health_v11_schema(client) -> None:
    health = _health(client)
    _require_v11(health)
    for key in ("engine", "features", "cache", "ocr", "quality", "providers"):
        assert key in health, f"/api/health 缺少 v1.1 字段 {key}"

    engine = health["engine"]
    assert isinstance(engine, dict), f"health.engine 应为对象，实际 {engine!r}"
    for key in ("name", "version", "revision"):
        assert key in engine, f"health.engine 缺字段 {key}: {engine}"

    features = health["features"]
    missing = [name for name in V11_HEALTH_FEATURES if name not in features]
    assert not missing, f"health.features 缺少 {missing}"

    cache = health["cache"]
    for key in ("dir", "files", "bytes", "entries"):
        assert key in cache, f"health.cache 缺字段 {key}: {cache}"

    version = str(health.get("version", ""))
    parts = version.split(".")
    try:
        major_minor = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        major_minor = (0, 0)
    assert major_minor >= (1, 1), f"v1.1 后端 health.version 仍为 {version}"


def test_models_endpoint(client, app_module) -> None:
    _require_route(app_module, "/api/models")
    status, payload = _get_json(client, "/api/models")
    assert status == 200, payload
    models = payload.get("models") if isinstance(payload, dict) else payload
    assert isinstance(models, list) and models, f"GET /api/models 应返回非空模型列表：{payload}"
    first = models[0]
    assert isinstance(first, dict) and first.get("id"), f"模型项缺少 id: {first}"


def test_test_connection_endpoint(client, app_module) -> None:
    _require_route(app_module, "/api/test-connection")
    status, payload = _get_json(
        client, "/api/test-connection", method="post", body={"base_url": "https://api.deepseek.com/v1"}
    )
    assert status == 200, payload
    assert "ok" in payload, f"/api/test-connection 应返回 ok 字段：{payload}"


def test_terminology_endpoints(client, app_module) -> None:
    _require_route(app_module, "/api/terminology")
    status, payload = _get_json(client, "/api/terminology")
    assert status == 200, payload
    assert isinstance(payload, (dict, list)), payload

    _require_route(app_module, "/api/terminology/search")
    status, payload = _get_json(client, "/api/terminology/search?q=attention")
    assert status == 200, payload
    assert isinstance(payload, (dict, list)), payload


def test_cache_delete_endpoint(client, app_module) -> None:
    _require_route(app_module, "/api/cache")
    status, payload = _get_json(client, "/api/cache", method="delete")
    assert status == 200, payload
    assert payload.get("ok") is True, f"DELETE /api/cache 应返回 ok=true：{payload}"


# ---------------------------------------------------------------------------
# v1.1 质量循环 / job detail / qa / cancel / history
# ---------------------------------------------------------------------------


def test_job_detail_v11_fields(client, app_module, sample_pdf) -> None:
    health = _health(client)
    _require_feature(health, "quality_loop")
    _require_route(app_module, "/api/jobs")

    job_id = _create_job(client, sample_pdf, quality_mode="fast", use_cache=False)
    detail = wait_job(client, job_id, timeout=240)
    assert detail.get("status") == "done", json.dumps(detail, ensure_ascii=False)[:800]
    missing = [field for field in V11_JOB_DETAIL_FIELDS if field not in detail]
    assert not missing, f"job detail 缺少 v1.1 字段 {missing}: {list(detail)}"
    assert detail.get("quality_mode") == "fast"
    timings = detail.get("timings_ms") or {}
    assert isinstance(timings, dict) and timings, f"timings_ms 应为非空对象：{timings}"

    # 进度语义：pages_done 是"已完成页数"（终态应等于 pages_total），
    # blocks_done/blocks_total 才是翻译块数；两者都不得越界。
    pages_done = detail.get("pages_done")
    pages_total = detail.get("pages_total")
    blocks_done = detail.get("blocks_done")
    blocks_total = detail.get("blocks_total")
    assert blocks_done is not None and blocks_total is not None, "job detail 缺少 blocks_done/blocks_total"
    if isinstance(pages_done, int) and isinstance(pages_total, int) and pages_total > 0:
        assert 0 <= pages_done <= pages_total, f"pages_done={pages_done} 越界 pages_total={pages_total}"
        assert pages_done == pages_total, f"终态 pages_done 应等于 pages_total：{pages_done}/{pages_total}"
    if isinstance(blocks_done, int) and isinstance(blocks_total, int) and blocks_total > 0:
        assert 0 <= blocks_done <= blocks_total, f"blocks_done={blocks_done} 越界 blocks_total={blocks_total}"

    qa_summary = detail.get("qa_summary") or {}
    for field in ("errors", "warnings", "total", "status", "report_url"):
        assert field in qa_summary, f"qa_summary 缺字段 {field}: {qa_summary}"

    files = detail.get("files") or {}
    for field in ("original_url", "translated_url"):
        assert field in files, f"job detail files 缺字段 {field}: {files}"


def test_qa_endpoint_schema(client, app_module, sample_pdf) -> None:
    health = _health(client)
    _require_feature(health, "quality_loop")
    _require_route(app_module, "/api/jobs")
    job_id = _create_job(client, sample_pdf, quality_mode="fast", use_cache=False)
    detail = wait_job(client, job_id, timeout=240)
    assert detail.get("status") == "done", detail.get("error")

    status, payload = _get_json(client, f"/api/jobs/{job_id}/qa")
    assert status == 200, f"任务已完成后 GET /api/jobs/{{id}}/qa 应返回 qa.json：{status} {payload}"
    missing = [field for field in QA_REQUIRED_FIELDS if field not in payload]
    assert not missing, f"qa.json 缺少附录 B 字段 {missing}"
    assert payload.get("status") in {"passed", "failed", "warning", "qa_failed"}, payload.get("status")
    assert payload.get("job_id") == job_id
    assert isinstance(payload.get("issues"), list)
    assert isinstance(payload.get("timings_ms"), dict)
    # 前端兼容别名：errors/warnings/total/codes（qa.json 文件 schema 不变）
    for alias in ("errors", "warnings", "total", "codes"):
        assert alias in payload, f"GET /api/jobs/{{id}}/qa 缺少前端兼容别名 {alias}: {list(payload)}"
    assert payload.get("errors") == payload.get("error_count")
    assert payload.get("warnings") == payload.get("warning_count")
    assert isinstance(payload.get("codes"), dict)


def test_qa_404_before_generation(client, app_module) -> None:
    _require_route(app_module, "/api/jobs/{job_id}/qa")
    status, payload = _get_json(client, "/api/jobs/nonexistent-job-id/qa")
    assert status == 404, payload


def test_cancel_contract(client, app_module, sample_pdf) -> None:
    health = _health(client)
    _require_feature(health, "cancel")
    _require_route(app_module, "/api/jobs/{job_id}/cancel")
    job_id = _create_job(client, sample_pdf, quality_mode="strict", use_cache=False)
    status, payload = _get_json(client, f"/api/jobs/{job_id}/cancel", method="post")
    # 允许与快速完成的任务竞争：已完成时返回 409 或 ok=false 都算契约成立
    assert status in (200, 409), payload
    if status == 200:
        assert isinstance(payload.get("ok"), bool), f"cancel 响应缺 ok: {payload}"
        assert payload.get("status"), f"cancel 响应缺 status: {payload}"
        assert payload["status"] in {
            "queued",
            "running",
            "cancelling",
            "cancelled",
            "canceled",
            "done",
            "error",
            "failed",
        }, payload
    detail = wait_job(client, job_id, timeout=240)
    assert detail.get("status") in {"cancelled", "canceled", "done", "error", "failed"}, detail


def test_history_v11_fields(client, app_module, sample_pdf) -> None:
    health = _health(client)
    _require_feature(health, "quality_loop")
    job_id = _create_job(client, sample_pdf, quality_mode="off", use_cache=False)
    detail = wait_job(client, job_id, timeout=240)
    assert detail.get("status") == "done", detail.get("error")

    status, history = _get_json(client, "/api/history")
    assert status == 200, history
    item = next((row for row in history.get("items", []) if row.get("job_id") == job_id), None)
    assert item is not None, f"历史记录未包含 {job_id}"
    for field in ("qa_summary", "quality_mode", "elapsed_ms", "pages", "blocks"):
        assert field in item, f"history item 缺少 v1.1 字段 {field}: {list(item)}"
    assert item.get("quality_mode") == "off"


def test_files_download_attachment(client, sample_pdf) -> None:
    """v1.1：?download=1 必须强制 attachment，普通访问保持 inline。"""
    _require_v11(_health(client))
    job_id = _create_job(client, sample_pdf)
    detail = assert_job_done(client, job_id)
    url = detail["result"]["translated_url"]

    plain = client.get(url)
    assert plain.status_code == 200
    assert "inline" in plain.headers.get("content-disposition", ""), plain.headers.get("content-disposition")

    download = client.get(url + ("&" if "?" in url else "?") + "download=1")
    assert download.status_code == 200, download.text[:300]
    disposition = download.headers.get("content-disposition", "")
    assert disposition.startswith("attachment"), f"download=1 未返回 attachment：{disposition}"


# ---------------------------------------------------------------------------
# v1.1 结构化错误
# ---------------------------------------------------------------------------


def test_structured_error_invalid_pdf(client, app_module) -> None:
    health = _health(client)
    _require_feature(health, "quality_loop")
    status, payload = _get_json(
        client, "/api/jobs", method="post", body={"pdf_path": "definitely-missing.pdf"}
    )
    assert status == 400, payload
    error = _error_payload(payload)
    assert error is not None, f"v1.1 错误响应缺少结构化 error 对象：{payload}"
    assert error.get("code") == "invalid_pdf", f"错误码应为 invalid_pdf：{error}"
    assert error.get("message"), f"错误缺少中文 message：{error}"
    assert "retryable" in error and "hint" in error, f"错误缺少 retryable/hint：{error}"
