# -*- coding: utf-8 -*-
"""候选制质量循环的纯逻辑层（不依赖 FastAPI / 引擎运行时）。

本模块只做「质量问题的分类与决策」，不执行任何 IO：

    * 四个候选动作：accept / repair_layout / retranslate / stop
    * issue → action 映射（版面类 / 可重译类 / 阻断类）
    * issue 去重与指纹（用于判断「重试后问题是否真的变了」）
    * 质量分 ``(errors, total)`` 的字典序比较
    * ``plan_quality_retry()``：根据本轮 QA 结果决定下一轮动作
    * strict 模式的验收闸门（error==0 且没有命中严格码的提醒）

设计参照 super_translate 的候选制质量循环，但把策略与执行解耦，
便于用表驱动单测覆盖所有分支（见 backend/tests/test_quality.py）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# qa.json 的 schema 版本；字段只增不改
QA_SCHEMA_VERSION = 1

# 质量档位（plan §1）：off 不跑 QA（旧行为）；fast 只体检；standard 体检+1 轮修复；
# strict 体检+最多 N 轮（N=PAPEREX_QA_MAX_PASSES，默认 4）。
QA_MODES: Tuple[str, ...] = ("off", "fast", "standard", "strict")
DEFAULT_QA_MODE = "standard"

# 单轮候选动作
class Action(str, Enum):
    ACCEPT = "accept"
    REPAIR_LAYOUT = "repair_layout"
    RETRANSLATE = "retranslate"
    STOP = "stop"


# ---------------------------------------------------------------------------
# issue 分类集合
# ---------------------------------------------------------------------------

# 版面类问题：重新排版即可改善（不需要重新请求翻译）
LAYOUT_REPAIR_ISSUE_CODES = frozenset(
    {
        "caption_overlap",
        "text_overlap",
        "text_visual_overlap",
        # paperex 引擎新增的版面类 code（plan §4 / port-spec B2）
        "raster_heading_body_overlap",
        "raster_caption_clearance",
        "raster_ink_overlap",
    }
)

# 可重译类问题：源文英语块没有被翻译 / 译文回退成英文，重译可能改善
RETRANSLATABLE_ISSUE_CODES = frozenset(
    {
        "untranslated_english",
        "untranslated_caption",
        "untranslated_formula_explanation",
        "untranslated_natural_language",
        "untranslated_block",
    }
)

# 阻断类问题：不是翻译/排版参数能解决的（PDF 结构损坏、页数不一致等）
BLOCKING_QA_ERROR_CODES = frozenset(
    {
        "qa_open_failed",
        "page_count_mismatch",
        "empty_page",
        "page_size_mismatch",
    }
)

# 默认 strict 闸门额外关注的提醒码；可用 PAPEREX_QA_STRICT_CODES 覆盖（逗号分隔）。
# 为空表示「只看 error==0」。
DEFAULT_STRICT_CODES: Tuple[str, ...] = ()


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return max(low, min(high, value))


def max_rounds_for_mode(mode: str) -> int:
    """每种质量档位最多允许的「修复候选轮数」。

    * off / fast  : 0（fast 只体检，不修复）
    * standard    : 1（plan §1：QA 一次 + 1 轮修复）
    * strict      : PAPEREX_QA_MAX_PASSES（默认 4，clamp 1..8）
    """
    mode = (mode or DEFAULT_QA_MODE).strip().lower()
    if mode == "standard":
        return 1
    if mode == "strict":
        return _env_int("PAPEREX_QA_MAX_PASSES", 4, low=1, high=8)
    return 0


def normalize_mode(mode: Optional[str]) -> str:
    value = (mode or "").strip().lower()
    if value in QA_MODES:
        return value
    return DEFAULT_QA_MODE


def strict_codes() -> frozenset:
    """PAPEREX_QA_STRICT_CODES：逗号分隔的 issue code 列表。"""
    raw = os.environ.get("PAPEREX_QA_STRICT_CODES")
    if raw is None:
        return frozenset(DEFAULT_STRICT_CODES)
    return frozenset(
        part.strip() for part in raw.split(",") if part.strip()
    )


# ---------------------------------------------------------------------------
# issue 规范化
# ---------------------------------------------------------------------------

def normalize_issue(issue: Any) -> Dict[str, Any]:
    """把 TranslationIssue（dataclass）或 dict 统一成 qa.json 的 issue 结构。"""
    if isinstance(issue, dict):
        page = issue.get("page")
        code = issue.get("code")
        severity = issue.get("severity")
        message = issue.get("message")
    else:
        page = getattr(issue, "page", None)
        code = getattr(issue, "code", None)
        severity = getattr(issue, "severity", "warning")
        message = getattr(issue, "message", "")
    try:
        page_value: Optional[int] = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_value = None
    return {
        "page": page_value,
        "code": str(code or "unknown"),
        "severity": str(severity or "warning"),
        "message": str(message or ""),
    }


def normalize_issues(issues: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    return [normalize_issue(issue) for issue in (issues or [])]


def issue_action(code: str, severity: str = "warning") -> str:
    """单个 issue 建议的处理动作（写入 qa.json 的 ``issues[].action``）。"""
    code = str(code or "")
    severity = str(severity or "warning")
    if code in BLOCKING_QA_ERROR_CODES:
        return Action.STOP.value
    if code in LAYOUT_REPAIR_ISSUE_CODES:
        return Action.REPAIR_LAYOUT.value
    if code in RETRANSLATABLE_ISSUE_CODES:
        return Action.RETRANSLATE.value
    if severity == "error":
        return Action.STOP.value
    return Action.ACCEPT.value


def annotate_issues(issues: Sequence[Any]) -> List[Dict[str, Any]]:
    """规范化并按 code/severity 补上 ``action`` 字段。"""
    payload: List[Dict[str, Any]] = []
    for issue in normalize_issues(issues):
        item = dict(issue)
        item["action"] = issue_action(item["code"], item["severity"])
        payload.append(item)
    return payload


def merge_issues(*groups: Sequence[Any]) -> List[Dict[str, Any]]:
    """合并多个 QA 来源并去重（page, code, message），保持出现顺序。"""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for issue in normalize_issues(group):
            key = (issue["page"], issue["code"], issue["message"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(issue)
    return merged


# ---------------------------------------------------------------------------
# 评分 / 指纹
# ---------------------------------------------------------------------------

def score(issues: Optional[Sequence[Any]]) -> Tuple[int, int]:
    """质量分：``(errors, total)``，字典序越小越好。"""
    normalized = normalize_issues(issues)
    errors = sum(1 for issue in normalized if issue["severity"] == "error")
    return errors, len(normalized)


def is_better(candidate: Tuple[int, int], best: Tuple[int, int]) -> bool:
    """候选必须「严格变好」才接受：``(errors, total)`` 字典序更小。"""
    return tuple(candidate) < tuple(best)


def issue_fingerprint(issues: Optional[Sequence[Any]]) -> str:
    """问题集合指纹：同一批问题重试后指纹相同 → 说明重试无效。"""
    normalized = normalize_issues(issues)
    keys = sorted(
        f"{issue['page']}|{issue['code']}|{issue['severity']}" for issue in normalized
    )
    digest = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()
    return digest[:16]


def count_by_severity(issues: Optional[Sequence[Any]]) -> Tuple[int, int]:
    normalized = normalize_issues(issues)
    errors = sum(1 for issue in normalized if issue["severity"] == "error")
    return errors, len(normalized) - errors


# ---------------------------------------------------------------------------
# strict 闸门
# ---------------------------------------------------------------------------

def strict_gate_ok(
    issues: Optional[Sequence[Any]],
    *,
    codes: Optional[Iterable[str]] = None,
) -> Tuple[bool, List[str]]:
    """strict 模式验收：error==0，且没有命中严格码的提醒。

    返回 ``(ok, reasons)``；reasons 是给 qa.json / 前端看的中文原因列表。
    """
    normalized = normalize_issues(issues)
    strict = frozenset(codes) if codes is not None else strict_codes()
    errors = [issue for issue in normalized if issue["severity"] == "error"]
    reasons: List[str] = []
    if errors:
        codes_hit = sorted({issue["code"] for issue in errors})
        reasons.append(f"仍有 {len(errors)} 个阻断问题（{', '.join(codes_hit)}）")
    if strict:
        hits = sorted(
            {issue["code"] for issue in normalized if issue["code"] in strict}
        )
        if hits:
            reasons.append(f"strict 严格码命中：{', '.join(hits)}")
    return (not reasons), reasons


# ---------------------------------------------------------------------------
# 规划下一轮动作
# ---------------------------------------------------------------------------

@dataclass
class RetryPlan:
    action: Action
    reason: str
    blocking_codes: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return self.action == Action.ACCEPT

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "blocking_codes": list(self.blocking_codes),
        }


def plan_quality_retry(
    issues: Optional[Sequence[Any]],
    *,
    previous_error_count: Optional[int] = None,
) -> RetryPlan:
    """根据本轮 QA 结果决定下一轮修复动作。

    优先级（plan §4）：

    1. 没有任何 issue            → ACCEPT
    2. 存在「error 级版面问题」  → REPAIR_LAYOUT
    3. 没有 error                → ACCEPT（只有提醒，交给 strict 闸门判断验收）
    4. 存在可重译类 error        → RETRANSLATE（若错误数没有比上一轮减少则 STOP）
    5. 其余（引擎修复才能解决）  → STOP
    """
    normalized = normalize_issues(issues)
    if not normalized:
        return RetryPlan(Action.ACCEPT, "本轮 QA 未发现问题")

    errors = [issue for issue in normalized if issue["severity"] == "error"]
    layout_errors = [
        issue for issue in errors if issue["code"] in LAYOUT_REPAIR_ISSUE_CODES
    ]
    if layout_errors:
        codes = sorted({issue["code"] for issue in layout_errors})
        return RetryPlan(
            Action.REPAIR_LAYOUT,
            f"发现 {len(layout_errors)} 个 {', '.join(codes)} 版面问题，可重新排版修复",
            tuple(codes),
        )

    if not errors:
        return RetryPlan(Action.ACCEPT, "只有提醒级问题，无需继续修复")

    retranslatable = [
        issue for issue in errors if issue["code"] in RETRANSLATABLE_ISSUE_CODES
    ]
    if not retranslatable:
        blocking = sorted({issue["code"] for issue in errors})
        return RetryPlan(
            Action.STOP,
            "剩余问题需要引擎级修复（重译/重排无法解决）",
            tuple(blocking),
        )

    if previous_error_count is not None and len(errors) >= int(previous_error_count):
        blocking = sorted({issue["code"] for issue in errors})
        return RetryPlan(
            Action.STOP,
            f"重试后阻断问题没有减少（{len(errors)} ≥ {previous_error_count}）",
            tuple(blocking),
        )

    codes = sorted({issue["code"] for issue in retranslatable})
    return RetryPlan(
        Action.RETRANSLATE,
        f"发现 {len(retranslatable)} 个未翻译文本块（{', '.join(codes)}），重新翻译后重试",
        tuple(codes),
    )


# ---------------------------------------------------------------------------
# 对外汇总（qa.py / main.py 共用）
# ---------------------------------------------------------------------------

def summarize(issues: Optional[Sequence[Any]]) -> Dict[str, Any]:
    """与旧版 ``summarize_issues`` 兼容的摘要（含 action）。"""
    annotated = annotate_issues(issues or [])
    errors, warnings = count_by_severity(annotated)
    codes: Dict[str, int] = {}
    for issue in annotated:
        codes[issue["code"]] = codes.get(issue["code"], 0) + 1
    return {
        "errors": errors,
        "warnings": warnings,
        "total": len(annotated),
        "codes": codes,
        "issues": annotated[:50],
    }


__all__ = [
    "Action",
    "BLOCKING_QA_ERROR_CODES",
    "DEFAULT_QA_MODE",
    "DEFAULT_STRICT_CODES",
    "LAYOUT_REPAIR_ISSUE_CODES",
    "QA_MODES",
    "QA_SCHEMA_VERSION",
    "RETRANSLATABLE_ISSUE_CODES",
    "RetryPlan",
    "annotate_issues",
    "count_by_severity",
    "is_better",
    "issue_action",
    "issue_fingerprint",
    "max_rounds_for_mode",
    "merge_issues",
    "normalize_issue",
    "normalize_issues",
    "normalize_mode",
    "plan_quality_retry",
    "score",
    "strict_codes",
    "strict_gate_ok",
    "summarize",
]
