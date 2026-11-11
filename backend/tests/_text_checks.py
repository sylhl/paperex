# -*- coding: utf-8 -*-
"""引擎文本层回归断言（D1-D6），供 pytest 与 ``_work/e2e`` 共用。

设计原则：
* 只依赖 PyMuPDF 文本层 + 引擎 QA 结果，不需要重跑翻译；
* 每个断言独立返回 ``CheckOutcome``，方便 e2e 汇总而 pytest 直接失败；
* 阈值基于 engine-defects.md 的验收条件，注释里记录缺陷现场（修复前基线）。

调用方约定：``translated_pdf`` 必须是**当前引擎**生成的产物；旧基线过期时
由调用方自行 skip。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pymupdf

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PLACEHOLDER_RE = re.compile(r"⟦\d+⟧|\ue000|\ufffd")
EN_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "is",
        "are",
        "was",
        "were",
        "with",
        "that",
        "this",
        "for",
        "by",
        "as",
        "from",
        "be",
        "which",
        "we",
        "our",
        "it",
        "at",
        "on",
        "not",
        "but",
        "has",
        "have",
        "can",
        "each",
        "when",
        "note",
        "than",
        "then",
    }
)
D5_FALSE_POSITIVE_CODES = {
    10: {"preserved_ink_mismatch"},
    11: {"formula_visible_ink_mismatch"},
    31: {"untranslated_english", "text_visual_overlap"},
}
FONT_ALIASES = ("zhbody", "zhbold")


@dataclass
class CheckOutcome:
    """一条文本层断言的结论。"""

    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def page_text(translated_pdf: Path, page: int) -> str:
    """1-based 页码取文本层。"""
    document = pymupdf.open(str(translated_pdf))
    try:
        return document[page - 1].get_text("text")
    finally:
        document.close()


def all_text(translated_pdf: Path) -> str:
    document = pymupdf.open(str(translated_pdf))
    try:
        return "\n".join(document[i].get_text("text") for i in range(document.page_count))
    finally:
        document.close()


def page_count(pdf_path: Path) -> int:
    document = pymupdf.open(str(pdf_path))
    try:
        return document.page_count
    finally:
        document.close()


def compare_documents(original_pdf: Path, translated_pdf: Path) -> int:
    """页数与源不一致时返回差异，一致返回 0。"""
    return page_count(translated_pdf) - page_count(original_pdf)


def band_words(translated_pdf: Path, page: int, y0: float, y1: float) -> List[str]:
    """取某页文本层中与纵向区间相交的单词（用于定位"整段英文残留"）。"""
    document = pymupdf.open(str(translated_pdf))
    try:
        words: List[str] = []
        for word in document[page - 1].get_text("words"):
            if word[3] > y0 and word[1] < y1:
                words.append(str(word[4]))
        return words
    finally:
        document.close()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


# ---------------------------------------------------------------------------
# 通用产物完整性
# ---------------------------------------------------------------------------


def check_page_count(original_pdf: Path, translated_pdf: Path) -> None:
    diff = compare_documents(original_pdf, translated_pdf)
    assert diff == 0, f"输出页数与源不一致：translated - source = {diff}"


def check_no_placeholder_leak(translated_pdf: Path) -> None:
    text = all_text(translated_pdf)
    leaked = PLACEHOLDER_RE.findall(text)
    assert not leaked, f"文本层出现占位符/替换符泄漏：{leaked[:8]}"


def check_chinese_rendered(translated_pdf: Path) -> None:
    text = all_text(translated_pdf)
    assert _cjk_count(text) >= 100, f"全文 CJK 字符过少（{_cjk_count(text)}），疑似未翻译/字体缺失"


def check_no_new_nul(original_pdf: Path, translated_pdf: Path) -> None:
    """D2：输出文本层不得引入源 PDF 中不存在的 NUL。

    源 PDF 本身可能含 NUL（如 DeepSeek_V4 源有 2 个），因此比较增量而非绝对值。
    """
    source = all_text(original_pdf).count("\x00")
    output = all_text(translated_pdf).count("\x00")
    assert output <= source, f"输出引入了 {output - source} 个 NUL（源 {source}，输出 {output}）"


def _ink_ratio(document, page_number: int, dpi: int) -> float:
    """渲染单页并统计深色像素占比（视觉 smoke：检测空白/漏页）。"""
    pixmap = document[page_number - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    samples = pixmap.samples
    if not samples:
        return 0.0
    dark = sum(1 for value in samples if value < 200)
    return dark / len(samples)


def check_visual_ink_similarity(original_pdf: Path, translated_pdf: Path, *, dpi: int = 50) -> None:
    """视觉 smoke：译文页不得出现异常空白（源页有内容而译文页近乎空白）。

    只看"空白页"这一强信号，不对中英文字体差异做比例断言，避免误报。
    """
    source_doc = pymupdf.open(str(original_pdf))
    translated_doc = pymupdf.open(str(translated_pdf))
    try:
        pages = min(source_doc.page_count, translated_doc.page_count)
        blank_pages = []
        for index in range(pages):
            source_ratio = _ink_ratio(source_doc, index + 1, dpi)
            translated_ratio = _ink_ratio(translated_doc, index + 1, dpi)
            if source_ratio > 0.01 and translated_ratio < max(0.001, 0.25 * source_ratio):
                blank_pages.append(
                    (index + 1, round(source_ratio, 3), round(translated_ratio, 3))
                )
        assert not blank_pages, f"译文疑似空白/漏页（页码, 源墨量, 译文墨量）：{blank_pages[:10]}"
    finally:
        source_doc.close()
        translated_doc.close()


# ---------------------------------------------------------------------------
# D1 / D2：3503221.3508418 首屏数字与脚注
# ---------------------------------------------------------------------------


def check_d1_numbers_3503221(translated_pdf: Path) -> None:
    """D1：右栏 ``1.37× - 17.87×`` 必须完整，且不得被 U+2011 替换连字符拆行。

    修复前现场：``.37× ‑ 17`` + ``.87×`` 两行，``1.37``/``17.87`` 均不存在。
    """
    text = page_text(translated_pdf, 1)
    start = re.search(r"1\.37\s*[×x]", text)
    end = re.search(r"17\.87\s*[×x]", text)
    assert start is not None, "p1 文本层缺少完整的 `1.37×`（仍被拆行）"
    assert end is not None, "p1 文本层缺少完整的 `17.87×`（仍被拆行）"
    span = text[start.start() : end.end()]
    assert "\u2011" not in span, f"数字区间出现替换连字符 U+2011：{span!r}"
    assert not re.search(r"\n1\s*\n\s*\.37", text), "数字仍被拆成 `1` + `.37` 两行"


def check_d2_no_nul_3503221(original_pdf: Path, translated_pdf: Path) -> None:
    """D2：源无 NUL，输出也必须无 NUL（脚注 `∗Corresponding author` 曾变 `\\x00Corresponding作者`）。"""
    output = all_text(translated_pdf).count("\x00")
    assert output == 0, f"3503221 输出文本层出现 {output} 个 NUL（源 PDF 为 0）"


# ---------------------------------------------------------------------------
# D3 / D4：DeepSeek_V4 第 10 / 11 页
# ---------------------------------------------------------------------------


def check_d3_page10_english(translated_pdf: Path) -> None:
    """D3：p10 顶部整段英文正文不得按保护区原样保留（原文见 engine-defects.md）。

    判据：该横带内英文停用词数量（中文译文/公式变量不会命中）+ 原文长句消失。
    修复前现场：带内停用词 > 15，``padded with negative infinity`` 原样存在。
    """
    text = page_text(translated_pdf, 10)
    normalized = _normalize(text)
    assert "padded with negative infinity" not in normalized, (
        "p10 仍保留英文正文片段 `padded with negative infinity`"
    )
    words = [word.lower().strip(".,;:()[]") for word in band_words(translated_pdf, 10, 280, 360)]
    stopword_hits = [word for word in words if word in EN_STOPWORDS]
    assert len(stopword_hits) <= 3, (
        f"p10 顶部横带仍残留整段英文正文：命中停用词 {len(stopword_hits)} 个 {stopword_hits[:12]}"
    )
    assert _cjk_count(_normalize(" ".join(band_words(translated_pdf, 10, 280, 360)))) >= 10, (
        "p10 顶部横带未出现中文译文"
    )


def check_d4_page11_formula(translated_pdf: Path) -> None:
    """D4：p11 公式原子 ``query=q_{t,i}`` 的 ``q`` 不得丢失（修复前为 ``uery=q``）。"""
    text = page_text(translated_pdf, 11)
    assert not re.search(r"(?<![A-Za-z])uery\s*=", text), "p11 仍存在缺首字母的 `uery=`（query 的 q 丢失）"
    assert re.search(r"query\s*=", text), "p11 文本层未恢复完整的 `query=` 公式原子"


# ---------------------------------------------------------------------------
# D5：QA 误报（需 qa.json 的 issues 列表）
# ---------------------------------------------------------------------------


def check_d5_qa_false_positives(issues: Sequence[dict]) -> None:
    """D5：table/algorithm/preserved 区域的误报必须消失，其余真实问题需保留（不在此断言）。"""
    offenders = []
    for issue in issues:
        try:
            page = int(issue.get("page", 0))
        except (TypeError, ValueError):
            continue
        code = str(issue.get("code", ""))
        if code in D5_FALSE_POSITIVE_CODES.get(page, set()):
            offenders.append(f"p{page}:{code}: {issue.get('message', '')[:100]}")
    assert not offenders, "DeepSeek_V4 QA 仍存在设计内保留区域的误报：" + "; ".join(offenders)


# ---------------------------------------------------------------------------
# D6：字体重名告警（已修复，回归防复发）
# ---------------------------------------------------------------------------


def check_d6_font_aliases(translated_pdf: Path) -> None:
    """D6：``invalid_font_aliases(doc, ('zhbody', 'zhbold')) == []``。"""
    from pdf_zh_translator.pdf_layout import invalid_font_aliases

    document = pymupdf.open(str(translated_pdf))
    try:
        broken = invalid_font_aliases(document, FONT_ALIASES)
    finally:
        document.close()
    assert not broken, f"输出 PDF 存在损坏的 CJK 字体别名：{broken}"


def check_no_damaged_font_warnings(warnings: Iterable[str]) -> None:
    """D6：生成日志中不得出现字体资源损坏类告警。"""
    bad = [
        str(item)
        for item in warnings
        if "damaged" in str(item) and ("font alias" in str(item) or "CJK font" in str(item))
    ]
    assert not bad, f"生成日志出现字体重名/损坏告警：{bad[:5]}"


# ---------------------------------------------------------------------------
# 汇总入口（e2e 使用；pytest 直接调单项断言）
# ---------------------------------------------------------------------------


def qa_issues_from_log(log: dict) -> List[dict]:
    qa = log.get("qa") or {}
    issues = qa.get("issues")
    return list(issues) if isinstance(issues, list) else []


def run_checks(
    stem: str,
    original_pdf: Path,
    translated_pdf: Path,
    *,
    qa_issues: Optional[Sequence[dict]] = None,
    warnings: Optional[Iterable[str]] = None,
) -> List[CheckOutcome]:
    """按论文执行全部适用断言，返回逐项结论（不抛异常）。"""
    outcomes: List[CheckOutcome] = []

    def record(name: str, func, *args, **kwargs) -> None:
        try:
            func(*args, **kwargs)
            outcomes.append(CheckOutcome(name, True))
        except AssertionError as exc:
            outcomes.append(CheckOutcome(name, False, str(exc)))
        except Exception as exc:  # noqa: BLE001 - 引擎内部异常也记录为失败
            outcomes.append(CheckOutcome(name, False, f"{type(exc).__name__}: {exc}"))

    record("page_count", check_page_count, original_pdf, translated_pdf)
    record("placeholder_leak", check_no_placeholder_leak, translated_pdf)
    record("chinese_rendered", check_chinese_rendered, translated_pdf)
    record("no_new_nul", check_no_new_nul, original_pdf, translated_pdf)
    record("visual_ink", check_visual_ink_similarity, original_pdf, translated_pdf, dpi=72)
    if stem == "3503221.3508418":
        record("d1_numbers", check_d1_numbers_3503221, translated_pdf)
        record("d2_nul", check_d2_no_nul_3503221, original_pdf, translated_pdf)
    if stem == "DeepSeek_V4":
        record("d3_page10_english", check_d3_page10_english, translated_pdf)
        record("d4_page11_formula", check_d4_page11_formula, translated_pdf)
        if qa_issues is not None:
            record("d5_qa_false_positives", check_d5_qa_false_positives, qa_issues)
    if qa_issues is None:
        record("qa_present", lambda: None)
    record("d6_font_aliases", check_d6_font_aliases, translated_pdf)
    if warnings is not None:
        record("d6_font_warnings", check_no_damaged_font_warnings, warnings)
    return outcomes
