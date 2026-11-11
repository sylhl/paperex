# -*- coding: utf-8 -*-
"""QA 隔离检查、报告 schema 与质量修复循环测试（无需 API key）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import qa
import qa_loop
import qa_worker
import quality
from jobs import JobCanceled

BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _text_pdf(path: Path, *, pages: int = 1, text: str = "Hello world. A short test.") -> Path:
    import fitz

    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), text, fontsize=12)
    document.save(str(path))
    document.close()
    return path


def _issue(code: str, severity: str = "error", page: int = 1, message: str = "") -> dict:
    return {
        "page": page,
        "code": code,
        "severity": severity,
        "message": message or f"{code} on page {page}",
    }


def _qa_result(issues, *, status=None, mode="standard") -> qa.QaResult:
    annotated = quality.annotate_issues([dict(item) for item in issues])
    return qa.QaResult(
        issues=annotated,
        status=status or qa.status_for(annotated, mode=mode),
        score=quality.score(annotated),
        fingerprint=quality.issue_fingerprint(annotated),
        timings_ms={"qa": 7},
    )


def _dummy_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4\n% dummy\n")
    return path


# ---------------------------------------------------------------------------
# qa_worker：issue 规整 / checker / spec 执行
# ---------------------------------------------------------------------------


class _FakeIssue:
    def __init__(self, page, code, message, severity="error"):
        self.page = page
        self.code = code
        self.message = message
        self.severity = severity


def test_issue_to_dict_from_object():
    item = _FakeIssue(2, "text_overlap", "文字重叠", "error")
    payload = qa_worker.issue_to_dict(item)
    assert payload == {
        "page": 2,
        "code": "text_overlap",
        "severity": "error",
        "message": "文字重叠",
    }


def test_issue_to_dict_from_dict_and_fallbacks():
    payload = qa_worker.issue_to_dict(
        {"page_index": 3, "kind": "untranslated_block", "reason": "未翻译", "severity": "weird"}
    )
    assert payload["page"] == 3
    assert payload["code"] == "untranslated_block"
    assert payload["severity"] == "warning"  # 未知 severity 归一到 warning


def test_issue_to_dict_none_and_truncation():
    assert qa_worker.issue_to_dict(None) is None
    payload = qa_worker.issue_to_dict(_FakeIssue(None, "x", "字" * 800))
    assert payload["page"] is None
    assert len(payload["message"]) == 500


def test_run_checker_unknown_raises(tmp_path):
    pdf = _text_pdf(tmp_path / "a.pdf")
    with pytest.raises(ValueError):
        qa_worker.run_checker("nope", pdf, pdf)


def test_run_checker_verify_and_inspect(tmp_path):
    original = _text_pdf(tmp_path / "original.pdf", pages=1)
    translated = _text_pdf(tmp_path / "translated.pdf", pages=1)
    for name in ("verify", "inspect"):
        issues = qa_worker.run_checker(name, original, translated)
        assert isinstance(issues, list)
        for item in issues:
            assert set(item) == {"page", "code", "severity", "message"}
            assert item["severity"] in {"error", "warning", "info"}
            assert len(item["message"]) <= 500


def test_run_spec_missing_paths():
    payload = qa_worker.run_spec({})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "qa_failed"
    assert payload["issues"] == []


def test_run_spec_basic(tmp_path):
    original = _text_pdf(tmp_path / "original.pdf")
    translated = _text_pdf(tmp_path / "translated.pdf")
    payload = qa_worker.run_spec(
        {"original_pdf": str(original), "translated_pdf": str(translated), "checkers": ["verify"]}
    )
    assert payload["ok"] is True
    assert payload["checkers"]["verify"]["ok"] is True
    assert isinstance(payload["issues"], list)
    assert payload["elapsed_ms"] >= 0


def test_run_spec_all_checkers_failed(tmp_path):
    payload = qa_worker.run_spec(
        {
            "original_pdf": str(tmp_path / "a.pdf"),
            "translated_pdf": str(tmp_path / "b.pdf"),
            "checkers": ["bogus"],  # 未知 checker 必然失败 → 全部失败
        }
    )
    assert payload["ok"] is False
    assert payload["checkers"]["bogus"]["ok"] is False
    assert "全部 QA 检查器失败" in payload["error"]["message"]


def test_worker_main_writes_result(tmp_path):
    original = _text_pdf(tmp_path / "original.pdf")
    translated = _text_pdf(tmp_path / "translated.pdf")
    result_path = tmp_path / "out" / "result.json"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "original_pdf": str(original),
                "translated_pdf": str(translated),
                "checkers": ["verify"],
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )
    assert qa_worker.main([str(spec_path)]) == 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["checkers"]["verify"]["ok"] is True


def test_worker_main_usage_errors(tmp_path):
    assert qa_worker.main([]) == 2
    assert qa_worker.main([str(tmp_path / "nope.json")]) == 2
    spec_path = tmp_path / "spec.json"
    spec_path.write_text("{}", encoding="utf-8")
    assert qa_worker.main([str(spec_path)]) == 2  # 缺 result_path


# ---------------------------------------------------------------------------
# qa.run_checkers：真实子进程 / 超时 / 无结果
# ---------------------------------------------------------------------------


def test_run_checkers_real_subprocess(tmp_path):
    original = _text_pdf(tmp_path / "original.pdf")
    translated = _text_pdf(tmp_path / "translated.pdf")
    payload = qa.run_checkers(
        original,
        translated,
        checkers=("verify",),
        timeout=120,
        scratch_dir=tmp_path / "scratch",
    )
    assert payload["qa_failed"] is False
    assert payload["checkers"]["verify"]["ok"] is True
    assert payload["checker_errors"] == []
    assert list((tmp_path / "scratch").glob("qa-verify-*.json"))


def test_run_checkers_timeout(tmp_path, monkeypatch):
    slow = tmp_path / "slow_worker.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(qa, "worker_path", lambda: slow)
    pdf = _dummy_pdf(tmp_path / "a.pdf")
    payload = qa.run_checkers(pdf, pdf, checkers=("verify", "inspect"), timeout=0.6, scratch_dir=tmp_path / "s")
    assert payload["qa_failed"] is True
    assert len(payload["checker_errors"]) == 2
    assert all("超时" in item["message"] for item in payload["checker_errors"])


def test_run_checkers_no_result(tmp_path, monkeypatch):
    broken = tmp_path / "broken_worker.py"
    broken.write_text(
        "import sys\nprint('boom: no result', file=sys.stderr)\nraise SystemExit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qa, "worker_path", lambda: broken)
    pdf = _dummy_pdf(tmp_path / "a.pdf")
    payload = qa.run_checkers(pdf, pdf, checkers=("verify",), timeout=60, scratch_dir=tmp_path / "s")
    assert payload["qa_failed"] is True
    message = payload["checker_errors"][0]["message"]
    assert "未产出结果" in message
    assert "exit=3" in message


# ---------------------------------------------------------------------------
# qa.evaluate / status / report
# ---------------------------------------------------------------------------


def test_status_for_table():
    failed = [_issue("text_overlap")]
    assert qa.status_for(failed, mode="standard") == "failed"
    assert qa.status_for([_issue("high_risk_layout", "warning")], mode="standard") == "warning"
    assert qa.status_for([], mode="standard") == "passed"
    assert qa.status_for([], mode="off") == "skipped"
    assert qa.status_for(failed, qa_failed=True) == "qa_failed"


def test_evaluate_with_fake_runner():
    raw_issues = [
        _issue("untranslated_english", "error", page=1),
        _issue("untranslated_english", "error", page=1),  # 去重
        _issue("high_risk_layout", "warning", page=2),
    ]

    def runner(original, translated, **kwargs):
        assert kwargs["checkers"] == qa.DEFAULT_CHECKERS
        return {
            "issues": raw_issues,
            "checkers": {"verify": {"ok": True, "issue_count": 3}},
            "qa_failed": False,
            "timings_ms": 11,
            "checker_errors": [],
        }

    result = qa.evaluate("o.pdf", "t.pdf", runner=runner)
    assert result.status == "failed"
    assert result.score == (1, 2)
    assert result.errors == 1
    assert result.warnings == 1
    assert len(result.fingerprint) == 16
    assert result.timings_ms == {"qa": 11}
    assert all("action" in issue for issue in result.issues)


def test_build_report_schema(tmp_path):
    result = qa.evaluate(
        "o.pdf",
        "t.pdf",
        runner=lambda *a, **k: {
            "issues": [_issue("untranslated_english"), _issue("high_risk_layout", "warning")],
            "checkers": {"verify": {"ok": True, "issue_count": 2}},
            "qa_failed": False,
            "timings_ms": 5,
        },
    )
    report = qa.build_report(
        job_id="abc12345",
        mode="standard",
        result=result,
        passes_run=2,
        repair_attempted=True,
        config_fingerprint="fp",
        cache_stats={"hits": 3, "misses": 2, "fallback_blocks": 1},
        timings_ms={"translate": 10, "qa": 5, "repair": 2, "total": 17},
    )
    for key in (
        "schema_version",
        "job_id",
        "generated_at",
        "qa_mode",
        "status",
        "passes_run",
        "repair_attempted",
        "engine",
        "config_fingerprint",
        "score",
        "best_score",
        "pass_history",
        "issue_count",
        "error_count",
        "warning_count",
        "issues",
        "issues_truncated",
        "terminology",
        "cache",
        "timings_ms",
    ):
        assert key in report, key
    assert report["schema_version"] == qa.QA_SCHEMA_VERSION == 1
    assert report["job_id"] == "abc12345"
    assert report["score"] == [1, 2]
    assert report["pass_history"][0]["agent_action"] == "baseline"
    assert report["issues_truncated"] is False
    assert {issue["code"] for issue in report["issues"]} == {
        "untranslated_english",
        "high_risk_layout",
    }
    assert report["issues"][0]["message"]
    assert report["engine"]["name"] == "pdf_zh_translator"
    assert report["terminology"]["candidates_added"] == 0
    assert report["cache"] == {"hits": 3, "misses": 2, "fallback_blocks": 1}
    assert report["timings_ms"]["total"] == 17


def test_build_report_best_score_from_history():
    result = _qa_result([_issue("text_overlap")])
    history = [
        {
            "pass": 0,
            "issue_count": 1,
            "error_count": 1,
            "warning_count": 0,
            "agent_action": "baseline",
            "agent_reason": "初始",
            "issue_fingerprint": result.fingerprint,
            "accepted": True,
        },
        {
            "pass": 1,
            "issue_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "agent_action": "repair_layout",
            "agent_reason": "重排版",
            "issue_fingerprint": "x",
            "accepted": True,
            "candidate_score": [0, 0],
        },
    ]
    report = qa.build_report(job_id="j", mode="standard", result=result, pass_history=history)
    assert report["best_score"] == [0, 0]
    assert report["score"] == [1, 1]


def test_write_load_report_and_summarize(tmp_path):
    result = _qa_result([_issue("text_overlap")])
    report = qa.build_report(job_id="j", mode="standard", result=result)
    target = qa.write_report(tmp_path / "qa.json", report)
    loaded = qa.load_report(target)
    assert loaded is not None and loaded["job_id"] == "j"
    summary = qa.summarize(dict(loaded, report_url="/api/jobs/j/qa"))
    assert summary == {
        "errors": 1,
        "warnings": 0,
        "total": 1,
        "status": "failed",
        "report_url": "/api/jobs/j/qa",
    }
    assert qa.load_report(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# qa_loop：接受 / 回滚 / 停止 / 取消 / 预算 / strict
# ---------------------------------------------------------------------------


def test_loop_repair_layout_accepted(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    candidate_pdf = _dummy_pdf(tmp_path / "candidate.pdf")
    initial = _qa_result([_issue("text_overlap")])
    candidate = _qa_result([])
    calls = []
    snapshots = []
    progress = []

    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        mode="standard",
        max_rounds=1,
        make_candidate=lambda round_no, action: (calls.append((round_no, action)) or candidate_pdf),
        run_qa=lambda path: candidate,
        on_progress=lambda *args: progress.append(args),
        snapshot=lambda round_no, pdf, result: snapshots.append((round_no, Path(pdf))),
        time_budget_s=60,
    )
    assert calls == [(1, quality.Action.REPAIR_LAYOUT)]
    assert progress == [(1, "repair_layout", 1)]
    assert outcome.repair_attempted is True
    assert outcome.accepted_rounds == 1
    assert outcome.best_pdf == candidate_pdf
    assert outcome.best_qa is candidate
    assert outcome.status == "done"
    assert "已零问题" in outcome.stopped_reason
    assert snapshots == [(1, candidate_pdf)]
    assert outcome.pass_history[0]["accepted"] is True
    assert outcome.quality_round() == {"current": 1, "max": 1, "status": "done"}


def test_loop_retranslate_stops_when_errors_do_not_drop(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    candidate_pdf = _dummy_pdf(tmp_path / "candidate.pdf")
    initial = _qa_result([
        _issue("untranslated_english", page=1),
        _issue("untranslated_english", page=2),
    ])
    candidate = _qa_result([_issue("untranslated_english", page=1)])
    actions = []

    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        mode="standard",
        max_rounds=3,
        make_candidate=lambda round_no, action: (actions.append(action) or candidate_pdf),
        run_qa=lambda path: candidate,
    )
    assert actions == [quality.Action.RETRANSLATE]
    assert outcome.passes_run == 1
    assert outcome.accepted_rounds == 1
    assert outcome.best_qa is candidate
    assert outcome.status == "stopped"
    assert "没有减少" in outcome.stopped_reason


def test_loop_rollback_when_not_better(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    candidate_pdf = _dummy_pdf(tmp_path / "candidate.pdf")
    initial = _qa_result([_issue("text_overlap")])
    candidate = _qa_result([_issue("text_overlap", message="other message")])
    snapshots = []

    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        max_rounds=2,
        make_candidate=lambda round_no, action: candidate_pdf,
        run_qa=lambda path: candidate,
        snapshot=lambda *args: snapshots.append(args),
    )
    assert outcome.accepted_rounds == 0
    assert outcome.best_pdf == initial_pdf
    assert outcome.best_qa is initial
    assert outcome.pass_history[0]["rolled_back"] is True
    assert outcome.status == "stopped"
    assert "指纹" in outcome.stopped_reason or "已回滚" in outcome.stopped_reason
    assert snapshots == []
    assert outcome.improved is False


def test_loop_no_problems_accepts_without_candidate(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    initial = _qa_result([_issue("high_risk_layout", "warning")])
    calls = []
    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        max_rounds=3,
        make_candidate=lambda *args: calls.append(args) or initial_pdf,
        run_qa=lambda path: initial,
    )
    assert calls == []
    assert outcome.passes_run == 0
    assert outcome.status == "done"
    assert "无需" in outcome.stopped_reason or "提醒" in outcome.stopped_reason


def test_loop_cancel_propagates(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    initial = _qa_result([_issue("text_overlap")])

    def cancel_check():
        raise JobCanceled("已取消")

    with pytest.raises(JobCanceled):
        qa_loop.run_quality_loop(
            initial_pdf=initial_pdf,
            initial_qa=initial,
            max_rounds=2,
            make_candidate=lambda *args: initial_pdf,
            run_qa=lambda path: _qa_result([]),
            cancel_check=cancel_check,
            cancel_exceptions=(JobCanceled,),
        )


def test_loop_candidate_failure_keeps_best(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    initial = _qa_result([_issue("text_overlap")])

    def failing_qa(path):
        raise RuntimeError("引擎崩溃")

    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        max_rounds=2,
        make_candidate=lambda *args: initial_pdf,
        run_qa=failing_qa,
    )
    assert outcome.status == "candidate_failed"
    assert outcome.best_pdf == initial_pdf
    assert outcome.best_qa is initial
    assert outcome.pass_history[0]["accepted"] is False
    assert "引擎崩溃" in outcome.pass_history[0]["error"]


def test_loop_time_budget_stops(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    initial = _qa_result([_issue("text_overlap")])
    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        max_rounds=2,
        make_candidate=lambda *args: initial_pdf,
        run_qa=lambda path: _qa_result([]),
        time_budget_s=0.0,
    )
    assert outcome.status == "time_budget"
    assert outcome.passes_run == 0
    assert "预算" in outcome.stopped_reason


def test_loop_zero_rounds(tmp_path):
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    initial = _qa_result([_issue("text_overlap")])
    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        max_rounds=0,
        make_candidate=lambda *args: initial_pdf,
        run_qa=lambda path: _qa_result([]),
    )
    assert outcome.passes_run == 0
    assert outcome.max_rounds == 0
    assert outcome.quality_round()["status"] == "skipped"


def test_loop_strict_gate_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPEREX_QA_STRICT_CODES", raising=False)
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    candidate_pdf = _dummy_pdf(tmp_path / "candidate.pdf")
    initial = _qa_result([_issue("text_overlap")])
    candidate = _qa_result([_issue("high_risk_layout", "warning")])
    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        mode="strict",
        max_rounds=3,
        make_candidate=lambda *args: candidate_pdf,
        run_qa=lambda path: candidate,
    )
    assert outcome.accepted_rounds == 1
    assert outcome.status == "done"
    assert "strict 门禁通过" in outcome.stopped_reason


def test_loop_strict_gate_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPEREX_QA_STRICT_CODES", "high_risk_layout")
    initial_pdf = _dummy_pdf(tmp_path / "initial.pdf")
    candidate_pdf = _dummy_pdf(tmp_path / "candidate.pdf")
    initial = _qa_result([_issue("text_overlap")])
    candidate = _qa_result([_issue("high_risk_layout", "warning")])
    outcome = qa_loop.run_quality_loop(
        initial_pdf=initial_pdf,
        initial_qa=initial,
        mode="strict",
        max_rounds=1,
        make_candidate=lambda *args: candidate_pdf,
        run_qa=lambda path: candidate,
    )
    assert outcome.accepted_rounds == 1
    assert outcome.status == "max_rounds"
    assert "最大修复轮数" in outcome.stopped_reason


def test_quality_time_budget_env(monkeypatch):
    monkeypatch.setenv("PAPEREX_QUALITY_BUDGET_S", "120")
    assert qa_loop.quality_time_budget_s() == 120.0
    monkeypatch.setenv("PAPEREX_QUALITY_BUDGET_S", "1")
    assert qa_loop.quality_time_budget_s() == 60.0
    monkeypatch.setenv("PAPEREX_QUALITY_BUDGET_S", "999999")
    assert qa_loop.quality_time_budget_s() == 7200.0
    monkeypatch.setenv("PAPEREX_QUALITY_BUDGET_S", "bad")
    assert qa_loop.quality_time_budget_s() == qa_loop.DEFAULT_TIME_BUDGET_S


def test_qa_timeout_env(monkeypatch):
    monkeypatch.setenv("PAPEREX_QA_TIMEOUT", "300")
    assert qa.qa_timeout_s() == 300.0
    monkeypatch.setenv("PAPEREX_QA_TIMEOUT", "1")
    assert qa.qa_timeout_s() == 30.0
    monkeypatch.setenv("PAPEREX_QA_TIMEOUT", "bad")
    assert qa.qa_timeout_s() == qa.DEFAULT_TIMEOUT_S
