# -*- coding: utf-8 -*-
"""离线自测：不需要任何 API Key，用引擎自带的 EchoTranslator 跑完整流水线。

覆盖点：
  1. 结构分类（标题 / 章节 / 图注 / 正文）
  2. 保护区与占位符（公式、图内文字不交给模型；⟦n⟧ 原样还原）
  3. 原位擦写与 CJK 排版
  4. 译后 QA：无占位符泄漏、无空白页、页数一致、图片与矢量保留

运行：
    cd backend && .venv/Scripts/python.exe -m pytest tests/test_engine_offline.py -q
或直接：
    cd backend && .venv/Scripts/python.exe tests/test_engine_offline.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pymupdf as fitz  # noqa: E402

from engine_adapter import discover_fonts, summarize_issues  # noqa: E402
from pdf_zh_translator.page_inspector import inspect_translation  # noqa: E402
from pdf_zh_translator.pdf_layout import (  # noqa: E402
    TextBlock,
    _align_body_paragraphs,
    _apply_toc_page_roles,
    _cjk_tracking_for,
    _demote_mixed_bold_headings,
    clean_translation,
    figure_caption_should_center,
    fill_leading_options,
    first_line_indent_for_block,
    leading_options,
    prepare_translation_units,
    translate_pdf,
    verify_translation_issues,
)
from pdf_zh_translator.translators import EchoTranslator  # noqa: E402


SAMPLE_TEXT = {
    "title": "Efficient Attention for Long-Context Language Models",
    "authors": "Alice Zhang, Bob Li, Carol Wang",
    "affiliation": "Department of Computer Science, Tsinghua University",
    "abstract": (
        "We propose a novel attention mechanism that reduces the quadratic cost of "
        "self-attention. Our method attains 2.3x speedup on long sequences while "
        "preserving accuracy, as shown in Figure 1 and Table 1."
    ),
    "section": "1 Introduction",
    "body": (
        "Transformers have become the dominant architecture for sequence modeling. "
        "The core operation is defined as A = softmax(QK^T / sqrt(d))V, where d is the "
        "channel dimension and Q, K, V are projections of the input. However, the "
        "memory cost grows quadratically with the sequence length, which limits the "
        "application to long documents [12]. Prior work tackles this issue with "
        "sparse patterns."
    ),
    "formula": "L(θ) = ∑ (y_i − f(x_i; θ))^2 + λ‖θ‖^2",
    "caption_fig": "Figure 1: Throughput comparison on 32k-token inputs.",
    "caption_tab": "Table 1: Accuracy of three baselines on the test split.",
}


def build_sample_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    page.insert_text((60, 90), SAMPLE_TEXT["title"], fontsize=19, fontname="helv")
    page.insert_text((60, 120), SAMPLE_TEXT["authors"], fontsize=10.5, fontname="helv")
    page.insert_text((60, 136), SAMPLE_TEXT["affiliation"], fontsize=9, fontname="helv")
    page.insert_text((60, 172), "Abstract", fontsize=12, fontname="hebo")
    page.insert_textbox(
        fitz.Rect(60, 180, 535, 250),
        SAMPLE_TEXT["abstract"],
        fontsize=9.5,
        fontname="helv",
        lineheight=1.45,
    )
    page.insert_text((60, 272), SAMPLE_TEXT["section"], fontsize=12, fontname="hebo")

    # 左栏正文（含行内公式 token）
    page.insert_textbox(
        fitz.Rect(60, 282, 292, 430),
        SAMPLE_TEXT["body"],
        fontsize=9.5,
        fontname="helv",
        lineheight=1.45,
    )
    # 右栏正文
    page.insert_textbox(
        fitz.Rect(305, 282, 535, 430),
        SAMPLE_TEXT["body"].replace("However", "Moreover"),
        fontsize=9.5,
        fontname="helv",
        lineheight=1.45,
    )

    # 展示公式（独立一行，符号密集）
    page.insert_text((150, 462), SAMPLE_TEXT["formula"], fontsize=11, fontname="helv")

    # 图：画一个矩形 + 图注
    page.draw_rect(fitz.Rect(80, 490, 260, 620), color=(0.2, 0.2, 0.2), fill=(0.93, 0.95, 0.98))
    page.draw_line(fitz.Point(90, 600), fitz.Point(250, 600), color=(0.4, 0.4, 0.9), width=1.5)
    page.insert_text((80, 640), SAMPLE_TEXT["caption_fig"], fontsize=8.5, fontname="helv")

    # 表格：多行数字网格 + 表注
    page.insert_text((330, 470), SAMPLE_TEXT["caption_tab"], fontsize=8.5, fontname="helv")
    rows = [
        ("Model", "Acc", "F1"),
        ("Baseline-A", "80.2", "77.1"),
        ("Baseline-B", "82.4", "79.3"),
        ("Ours", "85.9", "83.0"),
    ]
    y = 484
    for row in rows:
        page.insert_text((332, y), row[0], fontsize=8, fontname="helv")
        page.insert_text((430, y), row[1], fontsize=8, fontname="helv")
        page.insert_text((472, y), row[2], fontsize=8, fontname="helv")
        y += 14
    page.draw_line(fitz.Point(330, 478), fitz.Point(520, 478), color=(0, 0, 0), width=0.8)

    # 第二页：参考文献
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((60, 90), "References", fontsize=12, fontname="hebo")
    refs = [
        "[1] Vaswani, A. et al. Attention is all you need. In NeurIPS, 2017.",
        "[2] Beltagy, I. et al. Longformer: The long-document transformer. arXiv:2004.05150, 2020.",
        "[3] Dao, T. et al. FlashAttention: Fast and memory-efficient exact attention. 2022.",
    ]
    y = 112
    for ref in refs:
        page2.insert_textbox(
            fitz.Rect(60, y, 535, y + 30), ref, fontsize=8.5, fontname="helv", lineheight=1.3
        )
        y += 34
    page2.insert_textbox(
        fitz.Rect(60, 240, 535, 420),
        "3 Experiments We evaluate on three benchmarks and report mean accuracy over five "
        "seeds. All models are trained with the same recipe for 100k steps on eight A100 "
        "GPUs. The results confirm that the proposed method is significantly better.",
        fontsize=9.5,
        fontname="helv",
        lineheight=1.45,
    )

    doc.save(str(path))
    doc.close()


def run(tmp_dir: Path) -> dict:
    src = tmp_dir / "sample.pdf"
    out = tmp_dir / "sample.zh.pdf"
    build_sample_pdf(src)

    fonts = discover_fonts(
        Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        / "paperex"
        / "fonts"
    )
    assert fonts.regular is not None, "找不到可嵌入的中文字体"
    assert fonts.regular.name.startswith("LXGWWenKai"), fonts.regular.name
    assert fonts.bold is not None and fonts.bold.name.startswith("LXGWZhenKai"), (
        fonts.bold.name if fonts.bold else None
    )

    document = fitz.open(str(src))
    units, _gutters, skipped = prepare_translation_units(
        document, preserve_graphics_text=True
    )
    roles = Counter(block.block_type for block, _prompt, _mapping in units)
    document.close()

    report = translate_pdf(
        src,
        out,
        EchoTranslator(),
        font_file=fonts.regular,
        bold_font_file=fonts.bold,
        min_font_size=6.0,
        font_scale=0.90,
        preserve_graphics_text=True,
        harmonize_body_font_size=True,
    )
    issues = list(verify_translation_issues(src, out))
    try:
        issues.extend(inspect_translation(src, out))
    except Exception:
        pass

    return {
        "pages": report.page_count,
        "translatable": len(units),
        "skipped": skipped,
        "roles": dict(roles),
        "written": report.translated_blocks,
        "qa": summarize_issues(issues),
        "out": out,
    }


def check_layout_rules() -> None:
    """paperex 排版规则：行距、字距、段首对齐、run-in 粗体。"""
    heading = TextBlock(
        page_index=0,
        bbox=(318, 383, 560, 395),
        text="3.2.2 Cooperative Suspend-and-Yield Mechanism. Ex-",
        font_size=9.86,
        color=(0, 0, 0),
        bold=True,
        starts_bold=True,
        bold_terms=("3.2.2", "Cooperative Suspend-and-Yield Mechanism"),
        block_type="heading",
    )
    demoted = _demote_mixed_bold_headings([(heading, heading.text, {})])[0][0]
    assert demoted.block_type == "run_in_heading", "混合粗体标题未降级"
    assert not demoted.bold and demoted.bold_prefix, "降级后粗体范围错误"

    indented = TextBlock(
        page_index=0,
        bbox=(72, 200, 300, 260),
        text="   Indented paragraph with several source lines.",
        font_size=9.5,
        color=(0, 0, 0),
        source_lines=3,
        block_type="body",
    )
    aligned_block = TextBlock(
        page_index=0,
        bbox=(60, 270, 300, 330),
        text="Second paragraph on the same column.",
        font_size=9.5,
        color=(0, 0, 0),
        source_lines=3,
        block_type="body",
    )
    aligned = _align_body_paragraphs(
        [
            (indented, "   " + indented.text, {}),
            (aligned_block, aligned_block.text, {}),
        ]
    )
    assert aligned[0][0].bbox[0] == 60, "段落左边线未对齐"
    assert aligned[1][0].bbox[0] == 60, "段落左边线未对齐"
    assert aligned[0][0].text == indented.text.lstrip(), "段首空白未清理"

    assert leading_options(aligned_block)[0] == 1.52, "正文行距未调整为 1.52"
    assert leading_options(demoted)[0] != 1.52, "单行 run-in 标题不应套用正文行距"
    assert _cjk_tracking_for("中", 10.0) > 0, "CJK 字符缺少 tracking"
    assert _cjk_tracking_for("A", 10.0) == 0, "拉丁字符不应有 tracking"

    indented_line = TextBlock(
        page_index=0,
        bbox=(54, 100, 300, 130),
        text="Indented first line and continuation.",
        font_size=9.5,
        color=(0, 0, 0),
        source_lines=2,
        block_type="body",
        source_prose_bboxes=((64, 100, 300, 112), (54, 114, 300, 126)),
    )
    indent = first_line_indent_for_block(
        indented_line, fitz.Rect(54, 100, 300, 130), False
    )
    assert abs(indent - 10.0) < 0.01, "正文首行缩进未按源坐标计算"

    caption = TextBlock(
        page_index=0,
        bbox=(68, 243, 280, 256),
        text="Figure 4. The architecture overview of EdgeAgent",
        font_size=9.96,
        color=(0, 0, 0),
        source_lines=1,
        block_type="caption",
    )
    assert figure_caption_should_center(caption, [(54, 242), (318, 242)]), (
        "双栏图注未按栏居中"
    )
    assert not figure_caption_should_center(
        TextBlock(
            page_index=0,
            bbox=(54, 200, 296, 224),
            text="Table 1. Accuracy of three baselines.",
            font_size=9.0,
            color=(0, 0, 0),
            source_lines=2,
            block_type="caption",
        ),
        [(54, 242), (318, 242)],
    ), "表格题注不应强制居中"

    assert any(value > 1.52 for value in fill_leading_options(indented_line)), (
        "短译文应能通过增大行距填满源段落高度"
    )
    normalized = clean_translation("为 实现一种领域特定语言（领域特定语言）（）")
    assert "为 实现" not in normalized, "汉字之间不应保留空格"
    assert "（领域特定语言）" not in normalized, "重复括注未清理"
    assert "（）" not in normalized, "空括号未清理"

    toc_entry = TextBlock(
        page_index=0,
        bbox=(87.2, 171.9, 524.4, 182.8),
        text="2.1 Designs Inherited from DeepSeek-V3 . . . . . . 7",
        font_size=10.9,
        color=(0, 0, 0),
        source_lines=1,
        block_type="table",
        nowrap=True,
        should_translate=False,
        source_prose_bboxes=(
            (87.2, 171.9, 100.9, 182.8),
            (112.3, 171.9, 297.2, 182.8),
            (518.9, 171.9, 524.4, 182.8),
        ),
    )
    assert _apply_toc_page_roles([toc_entry], continuation=True), "目录页未识别"
    assert toc_entry.block_type == "toc_entry", "目录条目未转为 toc_entry"
    assert toc_entry.toc_number == "2.1" and toc_entry.toc_page == "7"
    assert abs(toc_entry.toc_title_x - 112.3) < 0.01, "目录标题 x 坐标错误"

    print("\n=== 排版规则 ===")
    print(
        "行距填充 / CJK tracking / 段首坐标 / 图注居中 / "
        "目录条目 / 译文清理 / run-in 粗体：OK"
    )


def check(result: dict) -> None:
    roles = result["roles"]
    assert result["translatable"] >= 8, f"可翻译块太少：{result['translatable']}"
    assert roles.get("title", 0) >= 1, "未识别出论文标题"
    assert roles.get("heading", 0) >= 2, "未识别出章节标题"
    assert roles.get("caption", 0) >= 2, "未识别出图注/表注"
    assert roles.get("body", 0) >= 3, "未识别出正文段"
    assert result["written"] >= 8, "回填块数过少"

    qa = result["qa"]
    assert qa["errors"] == 0, f"QA 阻断项：{qa['issues']}"
    assert qa["warnings"] == 0, f"QA 提醒：{qa['issues']}"
    assert "placeholder_leak" not in qa["codes"], "出现占位符泄漏"

    print("\n=== 统计 ===")
    print(
        "页数 %d | 可翻译 %d | 跳过 %d | 回填 %d"
        % (result["pages"], result["translatable"], result["skipped"], result["written"])
    )
    print("角色分布:", dict(sorted(roles.items())))
    print("QA:", {k: v for k, v in qa.items() if k != "issues"})


# ---------------------------------------------------------------------------
# pytest 入口（保留上面的 __main__ 直跑方式）
# ---------------------------------------------------------------------------


def test_offline_pipeline(tmp_path: Path) -> None:
    """离线 Echo 流水线全流程：结构分类 / 保护区 / 回填 / QA。"""
    result = run(tmp_path)
    check(result)
    out = result["out"]
    assert out.is_file(), f"输出 PDF 不存在：{out}"
    document = fitz.open(str(out))
    try:
        assert document.page_count == result["pages"], "输出页数与源不一致"
        assert document[0].get_text("text").strip(), "输出 PDF 首页文本层为空"
    finally:
        document.close()


def test_layout_rules() -> None:
    """排版规则：行距 / 字距 / 段首坐标 / 图注居中 / 目录 / 译文清理。"""
    check_layout_rules()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as name:
        result = run(Path(name))
        check_layout_rules()
        check(result)
        print("\n输出 PDF:", result["out"].name, "（临时目录，已随进程清理）")
    print("\nOK · 离线流水线全部通过")
