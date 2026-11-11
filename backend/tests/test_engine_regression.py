# -*- coding: utf-8 -*-
"""引擎文本层回归测试（D1-D6）。

用 ``_work/baseline/`` 下的真实样张产物做断言，不重跑翻译；产物过期
（引擎源码比基线新）时自动 skip 并提示重新生成命令，保证 CI 不会被
旧基线误红。

单独运行::

    cd paperex/backend
    .venv/Scripts/python.exe -m pytest tests/test_engine_regression.py -q

重新生成基线（真 API，需要 key，不进仓库）::

    python _work/baseline_all.py --only DeepSeek_V4 --force
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))

import _text_checks as tc  # noqa: E402

LAB_ROOT = Path(os.environ.get("PAPEREX_LAB_ROOT", BACKEND_DIR.parents[1]))
WORK_DIR = Path(os.environ.get("PAPEREX_WORK_DIR", LAB_ROOT / "_work"))
BASELINE_DIR = WORK_DIR / "baseline"
SOURCE_DIR = Path(os.environ.get("PAPEREX_SOURCE_PDF_DIR", LAB_ROOT / "new"))
ENGINE_DIR = BACKEND_DIR / "pdf_zh_translator"
MODEL = "deepseek-flash"

PAPERS = [
    "2103.13262v1",
    "3503221.3508418",
    "atc23-zhai",
    "DeepSeek_V4",
    "osdi26-jiang-yapeng",
]

# 已知缺陷论文（用于 D1-D5 专项断言）
D1_D2_PAPER = "3503221.3508418"
D3_D4_D5_PAPER = "DeepSeek_V4"


def _baseline_pdf(stem: str) -> Path:
    return BASELINE_DIR / f"{stem}.{MODEL}.zh.pdf"


def _baseline_log(stem: str) -> Path:
    return BASELINE_DIR / f"{stem}.{MODEL}.log.json"


def _source_pdf(stem: str) -> Path:
    return SOURCE_DIR / f"{stem}.pdf"


def _latest_engine_mtime() -> float:
    newest = 0.0
    for path in ENGINE_DIR.rglob("*.py"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _freshness_skip_reason(stem: str) -> str | None:
    """基线产物必须比引擎源码新，否则提示重新生成。"""
    if os.environ.get("PAPEREX_SKIP_FRESHNESS") == "1":
        return None
    pdf = _baseline_pdf(stem)
    log = _baseline_log(stem)
    if not pdf.exists() or not log.exists():
        return None  # 缺失由调用方按缺失处理
    newest_engine = _latest_engine_mtime()
    baseline_mtime = min(pdf.stat().st_mtime, log.stat().st_mtime)
    if newest_engine > baseline_mtime:
        return (
            f"基线产物早于引擎源码（engine mtime={newest_engine:.0f} > baseline={baseline_mtime:.0f}），"
            f"请重跑: python _work/baseline_all.py --only {stem} --force"
        )
    return None


def _require_artifacts(stem: str) -> tuple[Path, Path, Path]:
    source = _source_pdf(stem)
    pdf = _baseline_pdf(stem)
    log = _baseline_log(stem)
    missing = [str(path) for path in (source, pdf, log) if not path.exists()]
    if missing:
        pytest.skip(
            "缺少基线产物，无法做文本层回归："
            + ", ".join(missing)
            + f"；生成命令: python _work/baseline_all.py --only {stem} --force"
        )
    stale = _freshness_skip_reason(stem)
    if stale:
        pytest.skip(stale)
    return source, pdf, log


def _load_log(log_path: Path) -> dict:
    return json.loads(log_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("stem", PAPERS)
def test_render_completeness(stem: str) -> None:
    """5 篇通用产物完整性：页数、占位符泄漏、中文渲染、NUL 不新增。"""
    source, pdf, _log = _require_artifacts(stem)
    tc.check_page_count(source, pdf)
    tc.check_no_placeholder_leak(pdf)
    tc.check_chinese_rendered(pdf)
    tc.check_no_new_nul(source, pdf)


@pytest.mark.parametrize("stem", PAPERS)
def test_visual_smoke_no_blank_pages(stem: str) -> None:
    """视觉 smoke：不得出现源页有内容而译文页近乎空白的漏页。"""
    source, pdf, _log = _require_artifacts(stem)
    tc.check_visual_ink_similarity(source, pdf)


@pytest.mark.parametrize("stem", PAPERS)
def test_d6_font_aliases_are_clean(stem: str) -> None:
    """D6：输出 PDF 的 zhbody/zhbold 字体别名不得损坏（回归防复发）。"""
    _source, pdf, _log = _require_artifacts(stem)
    tc.check_d6_font_aliases(pdf)


@pytest.mark.parametrize("stem", PAPERS)
def test_d6_no_font_damage_warnings(stem: str) -> None:
    """D6：生成日志不得出现字体重名/资源损坏告警。"""
    _source, _pdf, log = _require_artifacts(stem)
    payload = _load_log(log)
    warnings = (payload.get("report") or {}).get("warnings") or []
    tc.check_no_damaged_font_warnings(warnings)


def test_d1_numbers_reassembled_3503221() -> None:
    """D1：p1 右栏 `1.37× - 17.87×` 必须完整（不得拆行、不得出现 U+2011 替换）。"""
    _source, pdf, _log = _require_artifacts(D1_D2_PAPER)
    tc.check_d1_numbers_3503221(pdf)


def test_d2_no_nul_3503221() -> None:
    """D2：p1 脚注 NUL 污染必须消失（源 PDF 无 NUL，输出必须为 0）。"""
    source, pdf, _log = _require_artifacts(D1_D2_PAPER)
    tc.check_d2_no_nul_3503221(source, pdf)


def test_d3_page10_english_removed() -> None:
    """D3：p10 顶部整段英文正文不得按保护区保留。"""
    _source, pdf, _log = _require_artifacts(D3_D4_D5_PAPER)
    tc.check_d3_page10_english(pdf)


def test_d4_page11_formula_atom() -> None:
    """D4：p11 公式 `query=q_{t,i}` 首字母不得丢失。"""
    _source, pdf, _log = _require_artifacts(D3_D4_D5_PAPER)
    tc.check_d4_page11_formula(pdf)


def test_d5_qa_false_positives_gone() -> None:
    """D5：DeepSeek_V4 QA 对 table/preserved/formula 的 5 处误报全部消失。"""
    _source, _pdf, log = _require_artifacts(D3_D4_D5_PAPER)
    issues = tc.qa_issues_from_log(_load_log(log))
    tc.check_d5_qa_false_positives(issues)


def test_d5_qa_still_detects_real_problems() -> None:
    """D5 反向保护：QA 不能把 D5 修复做成"关闭检测"。"""
    _source, _pdf, log = _require_artifacts(D3_D4_D5_PAPER)
    payload = _load_log(log)
    qa = payload.get("qa") or {}
    total = int(qa.get("total") or 0)
    # 修复后允许 total 下降，但 DeepSeek_V4 是 58 页双栏论文，仍应有布局类观察项
    assert total > 0, "DeepSeek_V4 QA 输出为空，疑似 D5 修复时误关闭了检测逻辑"
    codes = set(qa.get("codes") or [])
    assert codes, "QA codes 为空，疑似 issue 汇总逻辑被移除"


def test_d7_pages_parameter_supported() -> None:
    """D7：QA 分片所需的 pages 子集参数（0-based；None 等价全量；越界不抛）。"""
    from pdf_zh_translator import page_inspector
    from pdf_zh_translator import pdf_layout

    pending = []
    for name, func in (
        ("verify_translation_issues", pdf_layout.verify_translation_issues),
        ("inspect_translation", page_inspector.inspect_translation),
    ):
        parameters = inspect.signature(func).parameters
        if "pages" not in parameters:
            pending.append(f"{name}{inspect.signature(func)}")
    if pending:
        pytest.skip("D7 尚未实现（缺少 pages 参数）: " + "; ".join(pending))

    source, pdf, _log = _require_artifacts(D1_D2_PAPER)

    def fingerprint(issue: object) -> tuple:
        return (
            int(getattr(issue, "page", 0) or 0),
            str(getattr(issue, "code", "") or ""),
            str(getattr(issue, "message", "") or ""),
        )

    full = list(pdf_layout.verify_translation_issues(source, pdf))
    subset = list(pdf_layout.verify_translation_issues(source, pdf, [0]))
    assert all(int(getattr(issue, "page", 0) or 0) == 1 for issue in subset), (
        "pages=[0] 应只返回第 1 页（0-based）的 issue，实际页码："
        f"{sorted({int(getattr(issue, 'page', 0) or 0) for issue in subset})}"
    )
    assert {fingerprint(issue) for issue in subset} <= {fingerprint(issue) for issue in full}, (
        "pages 子集返回了全量结果之外的 issue"
    )
    assert list(pdf_layout.verify_translation_issues(source, pdf, [999])) == [], "越界页应返回空列表"

    inspected = page_inspector.inspect_translation(source, pdf, pages=[0])
    assert isinstance(inspected, list), f"inspect_translation 应返回 list，实际 {type(inspected)}"
    assert page_inspector.inspect_translation(source, pdf, pages=[999]) == [], "越界页应返回空列表"
