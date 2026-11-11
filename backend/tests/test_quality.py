# -*- coding: utf-8 -*-
"""quality.py 的表驱动单测（纯逻辑，不依赖引擎运行时）。"""

from __future__ import annotations

import importlib

import pytest

import quality
from quality import (
    Action,
    BLOCKING_QA_ERROR_CODES,
    LAYOUT_REPAIR_ISSUE_CODES,
    RETRANSLATABLE_ISSUE_CODES,
    is_better,
    issue_action,
    issue_fingerprint,
    max_rounds_for_mode,
    merge_issues,
    plan_quality_retry,
    score,
    strict_gate_ok,
)


def issue(code: str, severity: str = "warning", page: int = 1, message: str = "") -> dict:
    return {
        "page": page,
        "code": code,
        "severity": severity,
        "message": message or f"{code} on p{page}",
    }


# ---------------------------------------------------------------------------
# 分类集合 / 动作映射
# ---------------------------------------------------------------------------

ENGINE_EMITTED_CODES = {
    # 上游 28 个 + paperex 新增 11 个（见 port-spec B2）
    "caption_overlap",
    "text_overlap",
    "text_visual_overlap",
    "raster_heading_body_overlap",
    "raster_caption_clearance",
    "raster_ink_overlap",
    "untranslated_english",
    "untranslated_caption",
    "untranslated_formula_explanation",
    "untranslated_natural_language",
    "untranslated_block",
    "qa_open_failed",
    "page_count_mismatch",
    "empty_page",
    "page_size_mismatch",
    "formula_changed",
    "font_role_mismatch",
    "font_role_overflow",
    "font_role_missing",
    "font_role_too_small",
    "fragmented_prose",
    "high_risk_layout",
    "inline_formula_missing",
    "placeholder_leak",
    "visual_layout_region_low",
    "visual_layout_score_low",
    "terminology_missing",
    "terminology_conflict",
}


def test_codes_sets_are_disjoint():
    assert not (LAYOUT_REPAIR_ISSUE_CODES & RETRANSLATABLE_ISSUE_CODES)
    assert not (LAYOUT_REPAIR_ISSUE_CODES & BLOCKING_QA_ERROR_CODES)
    assert not (RETRANSLATABLE_ISSUE_CODES & BLOCKING_QA_ERROR_CODES)


@pytest.mark.parametrize(
    "code,severity,expected",
    [
        ("text_overlap", "error", Action.REPAIR_LAYOUT.value),
        ("caption_overlap", "warning", Action.REPAIR_LAYOUT.value),
        ("raster_heading_body_overlap", "error", Action.REPAIR_LAYOUT.value),
        ("untranslated_english", "error", Action.RETRANSLATE.value),
        ("untranslated_caption", "warning", Action.RETRANSLATE.value),
        ("qa_open_failed", "error", Action.STOP.value),
        ("page_count_mismatch", "error", Action.STOP.value),
        ("formula_changed", "error", Action.STOP.value),
        ("high_risk_layout", "warning", Action.ACCEPT.value),
        ("font_role_mismatch", "warning", Action.ACCEPT.value),
        ("terminology_conflict", "warning", Action.ACCEPT.value),
    ],
)
def test_issue_action_mapping(code, severity, expected):
    assert issue_action(code, severity) == expected


def test_issue_action_unknown_error_is_stop_unknown_warning_is_accept():
    assert issue_action("brand_new_code", "error") == Action.STOP.value
    assert issue_action("brand_new_code", "warning") == Action.ACCEPT.value


def test_engine_emitted_codes_covered_by_classification_or_stop():
    """引擎实发 code 必须落在三类集合或「STOP/ACCEPT」兜底里，不静默丢失。"""
    for code in ENGINE_EMITTED_CODES:
        action = issue_action(code, "error")
        assert action in {
            Action.REPAIR_LAYOUT.value,
            Action.RETRANSLATE.value,
            Action.STOP.value,
        }, code
        assert action == Action.STOP.value or code in (
            LAYOUT_REPAIR_ISSUE_CODES | RETRANSLATABLE_ISSUE_CODES | BLOCKING_QA_ERROR_CODES
        )


# ---------------------------------------------------------------------------
# normalize / merge / score / fingerprint
# ---------------------------------------------------------------------------

class FakeIssue:
    """模拟引擎的 TranslationIssue dataclass。"""

    def __init__(self, page, code, message, severity="warning"):
        self.page = page
        self.code = code
        self.message = message
        self.severity = severity


def test_normalize_issue_accepts_dataclass_and_dict():
    a = quality.normalize_issue(FakeIssue(3, "text_overlap", "x", "error"))
    b = quality.normalize_issue({"page": "3", "code": "text_overlap", "message": "x"})
    assert a == {"page": 3, "code": "text_overlap", "severity": "error", "message": "x"}
    assert b["page"] == 3 and b["severity"] == "warning"


def test_merge_issues_dedupes_by_page_code_message():
    merged = merge_issues(
        [issue("text_overlap", page=1), issue("text_overlap", page=2)],
        [issue("text_overlap", page=1), issue("caption_overlap", page=2)],
    )
    assert len(merged) == 3
    assert [i["page"] for i in merged] == [1, 2, 2]


def test_score_is_errors_then_total():
    issues = [
        issue("text_overlap", "error"),
        issue("high_risk_layout", "warning"),
        issue("font_role_mismatch", "warning"),
    ]
    assert score(issues) == (1, 3)
    assert score([]) == (0, 0)


def test_is_better_requires_strict_improvement():
    assert is_better((0, 0), (1, 2))
    assert is_better((1, 3), (1, 5))
    assert not is_better((1, 3), (1, 3))
    assert not is_better((2, 4), (1, 5))


def test_fingerprint_stable_and_order_independent():
    a = [issue("text_overlap", page=1), issue("font_role_mismatch", page=2)]
    b = list(reversed(a))
    assert issue_fingerprint(a) == issue_fingerprint(b)
    assert issue_fingerprint(a) != issue_fingerprint([issue("text_overlap", page=2)])
    assert len(issue_fingerprint(a)) == 16


# ---------------------------------------------------------------------------
# plan_quality_retry：表驱动
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "issues,previous,expected,reason_part",
    [
        # 无问题 → accept
        ([], None, Action.ACCEPT, "未发现问题"),
        # 只有提醒 → accept（strict 闸门另判）
        ([issue("high_risk_layout", "warning")], None, Action.ACCEPT, "只有提醒"),
        # error 级版面问题 → repair_layout
        ([issue("text_overlap", "error")], None, Action.REPAIR_LAYOUT, "版面"),
        ([issue("caption_overlap", "error"), issue("high_risk_layout", "warning")],
         None, Action.REPAIR_LAYOUT, "版面"),
        # warning 级版面问题不触发 repair_layout
        ([issue("text_overlap", "warning")], None, Action.ACCEPT, "只有提醒"),
        # 可重译 error → retranslate
        ([issue("untranslated_english", "error")], None, Action.RETRANSLATE, "未翻译"),
        # 引擎级 error → stop
        ([issue("formula_changed", "error")], None, Action.STOP, "引擎级修复"),
        # blocking code → stop 且带 blocking_codes
        ([issue("qa_open_failed", "error")], None, Action.STOP, "引擎级修复"),
        # 重试后错误数没减少 → stop
        ([issue("untranslated_english", "error")], 1, Action.STOP, "没有减少"),
        ([issue("untranslated_english", "error", page=1),
          issue("untranslated_caption", "error", page=2)], 1, Action.STOP, "没有减少"),
        # 重试后错误数减少 → 继续重译
        ([issue("untranslated_english", "error")], 2, Action.RETRANSLATE, "未翻译"),
    ],
)
def test_plan_quality_retry_table(issues, previous, expected, reason_part):
    plan = plan_quality_retry(issues, previous_error_count=previous)
    assert plan.action == expected, plan
    assert reason_part in plan.reason, plan.reason


def test_plan_blocking_codes_reported():
    plan = plan_quality_retry([issue("qa_open_failed", "error")])
    assert plan.blocking_codes == ("qa_open_failed",)


# ---------------------------------------------------------------------------
# strict 闸门
# ---------------------------------------------------------------------------

def test_strict_gate_requires_zero_errors():
    ok, reasons = strict_gate_ok([issue("text_overlap", "error")], codes=())
    assert not ok
    assert "阻断" in reasons[0]


def test_strict_gate_ignores_warnings_without_strict_codes():
    ok, reasons = strict_gate_ok([issue("high_risk_layout", "warning")], codes=())
    assert ok and reasons == []


def test_strict_gate_honours_strict_codes():
    ok, reasons = strict_gate_ok(
        [issue("high_risk_layout", "warning")], codes={"high_risk_layout"}
    )
    assert not ok
    assert "严格码" in reasons[0]


def test_strict_codes_env_override(monkeypatch):
    monkeypatch.setenv("PAPEREX_QA_STRICT_CODES", "high_risk_layout, placeholder_leak")
    assert quality.strict_codes() == {"high_risk_layout", "placeholder_leak"}
    monkeypatch.delenv("PAPEREX_QA_STRICT_CODES")
    assert quality.strict_codes() == set()


# ---------------------------------------------------------------------------
# 档位 → 轮数
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mode,expected",
    [("off", 0), ("fast", 0), ("standard", 1), ("strict", 4)],
)
def test_max_rounds_default(mode, expected, monkeypatch):
    monkeypatch.delenv("PAPEREX_QA_MAX_PASSES", raising=False)
    assert max_rounds_for_mode(mode) == expected


@pytest.mark.parametrize("raw,expected", [("0", 1), ("1", 1), ("3", 3), ("8", 8), ("99", 8)])
def test_strict_rounds_clamped(raw, expected, monkeypatch):
    monkeypatch.setenv("PAPEREX_QA_MAX_PASSES", raw)
    assert max_rounds_for_mode("strict") == expected


def test_normalize_mode_falls_back_to_standard():
    assert quality.normalize_mode("weird") == "standard"
    assert quality.normalize_mode(None) == "standard"
    assert quality.normalize_mode("STRICT") == "strict"


def test_module_importable_twice_is_stable():
    assert importlib.reload(quality).DEFAULT_QA_MODE == "standard"
