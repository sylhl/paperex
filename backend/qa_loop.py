# -*- coding: utf-8 -*-
"""候选制质量修复循环（plan §4）。

一轮 = 生成候选（重排版修复 / 重译）→ 隔离 QA → 严格变好才接受。
接受后更新最佳结果并写快照；否则丢弃候选、保留上一版。同指纹或无改善立即停，
任何一轮超出总时长预算也停并保留最佳候选。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import quality

DEFAULT_TIME_BUDGET_S = 1800.0


def quality_time_budget_s() -> float:
    raw = (os.environ.get("PAPEREX_QUALITY_BUDGET_S") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIME_BUDGET_S
    except ValueError:
        value = DEFAULT_TIME_BUDGET_S
    return max(60.0, min(7200.0, value))


@dataclass
class LoopOutcome:
    best_pdf: Path
    best_qa: Any
    passes_run: int = 0
    pass_history: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "stopped"
    stopped_reason: str = ""
    accepted_rounds: int = 0
    repair_attempted: bool = False
    max_rounds: int = 0

    @property
    def improved(self) -> bool:
        return self.accepted_rounds > 0

    def quality_round(self) -> Dict[str, Any]:
        if not self.max_rounds:
            return {"current": 0, "max": 0, "status": "skipped"}
        return {
            "current": self.passes_run,
            "max": self.max_rounds,
            "status": "done" if self.status in ("done", "max_rounds") else self.status,
        }


def _score(issues: Any) -> tuple:
    return quality.score(list(issues or []))


def run_quality_loop(
    *,
    initial_pdf: Path,
    initial_qa: Any,
    mode: str = "standard",
    max_rounds: int = 1,
    make_candidate: Callable[[int, quality.Action], Path],
    run_qa: Callable[[Path], Any],
    on_progress: Optional[Callable[[int, str, int], None]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    snapshot: Optional[Callable[[int, Path, Any], None]] = None,
    started_at: Optional[float] = None,
    time_budget_s: Optional[float] = None,
    cancel_exceptions: tuple = (),
) -> LoopOutcome:
    """跑质量修复循环。

    参数
    ----
    initial_pdf / initial_qa : 首轮翻译结果与其 QA 结论
    make_candidate(round, action) : 生成候选 PDF 路径（main 负责实际翻译/重排版）
    run_qa(pdf) : 对候选跑隔离 QA，返回 ``qa.QaResult``
    on_progress : 阶段进度回调（main 用来更新 job.quality.round）
    cancel_check : 抛 ``JobCanceled`` 的协作式取消检查
    snapshot(round, pdf, qa) : 接受候选后写 ``.snapshots/round-<n>/``
    """
    started = time.time() if started_at is None else float(started_at)
    budget = quality_time_budget_s() if time_budget_s is None else float(time_budget_s)
    best_pdf = Path(initial_pdf)
    best_qa = initial_qa
    best_score = _score(best_qa.issues)
    history: List[Dict[str, Any]] = []
    outcome = LoopOutcome(
        best_pdf=best_pdf,
        best_qa=best_qa,
        max_rounds=max(0, int(max_rounds)),
    )
    previous_error_count: Optional[int] = None
    for round_no in range(1, outcome.max_rounds + 1):
        if cancel_check is not None:
            cancel_check()
        elapsed = time.time() - started
        if elapsed > budget:
            outcome.status = "time_budget"
            outcome.stopped_reason = f"总时长超出预算（{budget:.0f}s），保留当前最佳结果"
            break
        plan = quality.plan_quality_retry(best_qa.issues, previous_error_count=previous_error_count)
        if plan.action in (quality.Action.ACCEPT, quality.Action.STOP):
            outcome.stopped_reason = plan.reason
            outcome.status = "done" if plan.action is quality.Action.ACCEPT else "stopped"
            break
        # 只要进入候选生成阶段（重排版或重译）就算尝试过修复；
        # 文档中的 repair_attempted 语义是「是否尝试过修复」，与具体动作无关。
        outcome.repair_attempted = True
        if on_progress is not None:
            on_progress(round_no, plan.action.value, outcome.max_rounds)
        outcome.passes_run = round_no
        try:
            candidate_pdf = Path(make_candidate(round_no, plan.action))
            candidate_qa = run_qa(candidate_pdf)
        except Exception as exc:  # 候选生成/QA 失败：保留最佳，停止循环
            if cancel_exceptions and isinstance(exc, cancel_exceptions):
                raise  # 取消必须向上传播
            history.append(
                {
                    "pass": round_no,
                    "issue_count": len(getattr(best_qa, "issues", None) or []),
                    "error_count": best_score[0],
                    "warning_count": max(0, best_score[1] - best_score[0]),
                    "agent_action": plan.action.value,
                    "agent_reason": plan.reason,
                    "issue_fingerprint": getattr(best_qa, "fingerprint", ""),
                    "accepted": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            outcome.status = "candidate_failed"
            outcome.stopped_reason = f"候选生成失败，保留最佳结果：{exc}"
            break
        candidate_score = _score(candidate_qa.issues)
        candidate_fingerprint = getattr(candidate_qa, "fingerprint", "")
        better = quality.is_better(candidate_score, best_score)
        same_fingerprint = bool(candidate_fingerprint) and candidate_fingerprint == getattr(
            best_qa, "fingerprint", ""
        )
        accepted = better and not same_fingerprint
        entry: Dict[str, Any] = {
            "pass": round_no,
            "issue_count": len(candidate_qa.issues),
            "error_count": candidate_score[0],
            "warning_count": max(0, candidate_score[1] - candidate_score[0]),
            "agent_action": plan.action.value,
            "agent_reason": plan.reason,
            "issue_fingerprint": candidate_fingerprint,
            "accepted": accepted,
            "candidate_score": [int(candidate_score[0]), int(candidate_score[1])],
            "previous_score": [int(best_score[0]), int(best_score[1])],
        }
        if accepted:
            best_pdf = candidate_pdf
            best_qa = candidate_qa
            best_score = candidate_score
            previous_error_count = candidate_score[0]
            outcome.best_pdf = best_pdf
            outcome.best_qa = best_qa
            outcome.accepted_rounds += 1
            entry["score_after"] = [int(best_score[0]), int(best_score[1])]
            if snapshot is not None:
                try:
                    snapshot(round_no, best_pdf, best_qa)
                except Exception as exc:  # 快照失败不影响主流程
                    entry["snapshot_error"] = f"{type(exc).__name__}: {exc}"
            history.append(entry)
            if best_score == (0, 0):
                outcome.status = "done"
                outcome.stopped_reason = "已零问题"
                break
            if mode == "strict":
                ok, reasons = quality.strict_gate_ok(best_qa.issues, codes=quality.strict_codes())
                if ok:
                    outcome.status = "done"
                    outcome.stopped_reason = "strict 门禁通过：" + ("；".join(reasons) or "无问题")
                    break
            continue
        entry["rolled_back"] = True
        history.append(entry)
        outcome.status = "stopped"
        outcome.stopped_reason = (
            f"候选未严格变好（{best_score} → {candidate_score}），已回滚"
            if not same_fingerprint
            else "候选问题指纹与当前一致，停止迭代"
        )
        break
    else:
        if outcome.max_rounds:
            outcome.status = "max_rounds"
            outcome.stopped_reason = outcome.stopped_reason or "已达到最大修复轮数"
    outcome.pass_history = history
    return outcome


__all__ = [
    "DEFAULT_TIME_BUDGET_S",
    "LoopOutcome",
    "quality_time_budget_s",
    "run_quality_loop",
]
