# -*- coding: utf-8 -*-
"""独立 QA 子进程 worker（plan §4「隔离 QA」）。

调用方式::

    python qa_worker.py <spec.json>

spec.json::

    {
      "original_pdf": "...", "translated_pdf": "...",
      "checkers": ["verify", "inspect"], "max_pages": null,
      "result_path": "..."
    }

result JSON::

    {"ok": true, "issues": [{page, code, severity, message}],
     "checkers": {"verify": {ok, issue_count, elapsed_ms}, ...},
     "elapsed_ms": 123}

子进程崩溃/超时由主进程兜底；本进程内任何 checker 异常都写进结构化
``error`` 字段而不是抛出，保证 ``result_path`` 尽量落盘。
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CHECKERS = ("verify", "inspect")


def _ensure_engine_path() -> None:
    backend_dir = Path(__file__).resolve().parent
    for candidate in (backend_dir, backend_dir.parent):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)


def issue_to_dict(item: Any) -> Optional[Dict[str, Any]]:
    """把引擎 issue（dataclass / dict / 自定义对象）规整成 JSON 友好字典。"""
    if item is None:
        return None
    if isinstance(item, dict):
        source: Dict[str, Any] = dict(item)
    else:
        source = {}
        for attr in ("page", "page_index", "code", "kind", "message", "reason", "detail", "severity"):
            if hasattr(item, attr):
                source[attr] = getattr(item, attr)
    page = source.get("page", source.get("page_index"))
    try:
        page_number = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_number = None
    code = str(source.get("code") or source.get("kind") or "unknown")
    message = source.get("message") or source.get("reason") or source.get("detail") or ""
    severity = str(source.get("severity") or "warning").lower()
    if severity not in ("error", "warning", "info"):
        severity = "warning"
    return {
        "page": page_number,
        "code": code,
        "severity": severity,
        "message": str(message)[:500],
    }


def run_checker(
    name: str,
    original_pdf: Path,
    translated_pdf: Path,
    *,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if name == "verify":
        from pdf_zh_translator.pdf_layout import verify_translation_issues

        issues = verify_translation_issues(Path(original_pdf), Path(translated_pdf))
    elif name == "inspect":
        from pdf_zh_translator.page_inspector import inspect_translation

        kwargs: Dict[str, Any] = {}
        if max_pages:
            kwargs["max_pages"] = int(max_pages)
        issues = inspect_translation(Path(original_pdf), Path(translated_pdf), **kwargs)
    else:
        raise ValueError(f"未知 checker: {name}")
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in issues or []:
        normalized = issue_to_dict(item)
        if normalized is None:
            continue
        key = (normalized["page"], normalized["code"], normalized["message"])
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def run_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    payload: Dict[str, Any] = {
        "ok": True,
        "issues": [],
        "checkers": {},
        "elapsed_ms": 0,
    }
    original = spec.get("original_pdf")
    translated = spec.get("translated_pdf")
    if not original or not translated:
        payload["ok"] = False
        payload["error"] = {
            "code": "qa_failed",
            "message": "QA spec 缺少 original_pdf / translated_pdf",
        }
        payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return payload
    try:
        _ensure_engine_path()
        from pdf_zh_translator.page_inspector import inspect_translation  # noqa: F401 - 预热导入
    except Exception as exc:  # pragma: no cover - 引擎缺失时结构化返回
        payload["ok"] = False
        payload["error"] = {
            "code": "qa_failed",
            "message": f"QA 引擎不可用：{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=3),
        }
        payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    checkers = list(spec.get("checkers") or DEFAULT_CHECKERS)
    max_pages = spec.get("max_pages")
    all_issues: List[Dict[str, Any]] = []
    for checker in checkers:
        checker_started = time.perf_counter()
        try:
            found = run_checker(checker, Path(original), Path(translated), max_pages=max_pages)
        except Exception as exc:
            payload["checkers"][checker] = {
                "ok": False,
                "issue_count": 0,
                "elapsed_ms": int((time.perf_counter() - checker_started) * 1000),
                "error": {
                    "code": "qa_failed",
                    "message": f"{checker} 检查失败：{type(exc).__name__}: {exc}",
                },
            }
            continue
        payload["checkers"][checker] = {
            "ok": True,
            "issue_count": len(found),
            "elapsed_ms": int((time.perf_counter() - checker_started) * 1000),
        }
        all_issues.extend(found)

    payload["issues"] = all_issues
    failures = [name for name, info in payload["checkers"].items() if not info.get("ok")]
    if checkers and len(failures) == len(checkers):
        payload["ok"] = False
        payload["error"] = {
            "code": "qa_failed",
            "message": "全部 QA 检查器失败：" + "；".join(
                str(payload["checkers"][name].get("error", {}).get("message", name)) for name in failures
            ),
        }
    payload["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return payload


def _write_result(result_path: Path, payload: Dict[str, Any]) -> None:
    result_path = Path(result_path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = result_path.with_suffix(result_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(result_path)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python qa_worker.py <spec.json>", file=sys.stderr)
        return 2
    spec_path = Path(args[0])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"无法读取 QA spec: {exc}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        print("QA spec 必须是 JSON 对象", file=sys.stderr)
        return 2
    result_path = spec.get("result_path")
    if not result_path:
        print("QA spec 缺少 result_path", file=sys.stderr)
        return 2
    payload = run_spec(spec)
    try:
        _write_result(Path(result_path), payload)
    except OSError as exc:  # pragma: no cover - 磁盘异常
        print(f"无法写入 QA 结果: {exc}", file=sys.stderr)
        return 3
    print(
        f"qa_worker: ok={payload['ok']} issues={len(payload['issues'])} "
        f"elapsed={payload['elapsed_ms']}ms"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
