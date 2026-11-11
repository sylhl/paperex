# -*- coding: utf-8 -*-
"""扫描版 PDF 的 OCR 预处理（plan §6）。

本机没有 Tesseract 时绝不崩溃：所有失败都以结构化错误
(``ocr_required`` / ``ocr_unavailable``) 抛出，由应用层转成带中文
hint 的 API 错误。模式由 ``PAPEREX_OCR`` 控制：

- ``off``  ：不做 OCR，无文本层直接报 ``no_text_layer``；
- ``auto`` ：仅在检测到扫描版时 OCR（默认）；
- ``force``：总是先 OCR 再翻译（可修复文本层残缺的 PDF）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

OCR_MODES = ("off", "auto", "force")
DEFAULT_OCR_MODE = "auto"

INSTALL_HINT = (
    "本机未检测到 Tesseract OCR。请安装 Tesseract 5.x 并将 tesseract 加入 PATH"
    "（Windows 可用 winget install UB-Mannheim.TesseractOCR），或设置 PAPEREX_TESSDATA "
    "指向 tessdata 目录后重启后端；也可以直接上传带文本层的 PDF。"
)

MODE_HINT = "PAPEREX_OCR 控制 OCR 行为：off=不启用、auto=扫描版自动 OCR、force=总是先 OCR。"


class OcrError(RuntimeError):
    """带结构化 code / hint 的 OCR 错误，供 engine_adapter.classify_error 识别。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable


def ocr_mode() -> str:
    value = (os.environ.get("PAPEREX_OCR") or DEFAULT_OCR_MODE).strip().lower()
    return value if value in OCR_MODES else DEFAULT_OCR_MODE


def tesseract_path() -> Optional[str]:
    override = (os.environ.get("PAPEREX_TESSERACT") or "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("tesseract")
    return found or None


def tessdata_dir() -> Optional[Path]:
    override = (os.environ.get("PAPEREX_TESSDATA") or "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None
    exe = tesseract_path()
    if exe:
        candidate = Path(exe).resolve().parent / "tessdata"
        if candidate.is_dir():
            return candidate
    return None


def tesseract_version() -> Optional[str]:
    exe = tesseract_path()
    if not exe:
        return None
    try:
        import subprocess

        completed = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first = (completed.stdout or completed.stderr or "").strip().splitlines()
        if first:
            return first[0].strip()[:80]
    except (OSError, ValueError):  # pragma: no cover - 环境相关
        return None
    return None


def is_available() -> bool:
    return tesseract_path() is not None


def status() -> Dict[str, Any]:
    """health 的 ``ocr`` 字段。"""
    available = is_available()
    return {
        "available": available,
        "engine": "tesseract" if available else None,
        "version": tesseract_version() if available else None,
        "mode": ocr_mode(),
        "hint": "" if available else INSTALL_HINT,
    }


def _text_page_count(pdf_path: Path, *, min_chars: int = 20) -> Dict[str, Any]:
    """统计 PDF 页数与「有文本层」的页数（不依赖引擎可选模块）。"""
    import fitz

    with fitz.open(str(pdf_path)) as document:
        if getattr(document, "needs_pass", False):
            raise OcrError("encrypted_pdf", "PDF 已加密，无法读取文本层", hint="请先解除 PDF 密码后再上传")
        pages = int(document.page_count)
        text_pages = 0
        for page in document:
            try:
                text = page.get_text("text") or ""
            except Exception:  # pragma: no cover - 损坏页兜底
                text = ""
            if len(text.strip()) >= min_chars:
                text_pages += 1
    return {"pages": pages, "text_pages": text_pages}


def analyze(pdf_path: Path, *, min_text_ratio: float = 0.2) -> Dict[str, Any]:
    """分析文本层情况：``needs_ocr`` 为 True 表示需要 OCR。"""
    info = _text_page_count(Path(pdf_path))
    pages = int(info["pages"])
    text_pages = int(info["text_pages"])
    needs_ocr = pages > 0 and (text_pages == 0 or text_pages / pages < min_text_ratio)
    return {
        "pages": pages,
        "text_pages": text_pages,
        "text_ratio": round(text_pages / pages, 3) if pages else 0.0,
        "needs_ocr": needs_ocr,
    }


def _require_engine() -> Any:
    if not is_available():
        raise OcrError(
            "ocr_unavailable",
            "本机未安装 Tesseract OCR，无法对扫描版 PDF 做文字识别",
            hint=INSTALL_HINT,
        )
    try:
        from pdf_zh_translator import ocr as engine_ocr  # type: ignore
    except Exception as exc:  # pragma: no cover - 引擎缺模块时兜底
        raise OcrError(
            "engine_error",
            f"引擎 OCR 模块不可用：{exc}",
            hint="请检查 pdf_zh_translator 安装是否完整",
        ) from exc
    return engine_ocr


def run_ocr(
    source_pdf: Path,
    output_pdf: Path,
    *,
    language: str = "eng",
    dpi: int = 180,
) -> Dict[str, Any]:
    """对整份 PDF 做 OCR，生成带文本层的副本；返回结构化报告。"""
    source_pdf = Path(source_pdf)
    output_pdf = Path(output_pdf)
    engine_ocr = _require_engine()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    try:
        engine_ocr.ocr_pdf_to_searchable(
            source_pdf,
            output_pdf,
            language=language,
            dpi=dpi,
            tessdata=tessdata_dir(),
        )
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(
            "ocr_unavailable",
            f"OCR 失败：{exc}",
            hint=INSTALL_HINT if not is_available() else "请确认 tessdata 包含所需语言包",
            retryable=True,
        ) from exc
    before = analyze(source_pdf)
    after = analyze(output_pdf)
    if after["text_pages"] == 0 and after["pages"] > 0:
        raise OcrError(
            "ocr_required",
            "OCR 已完成但仍未提取到文本层，可能扫描件分辨率过低或语言包不匹配",
            hint="请尝试 300dpi 重新扫描，或安装对应语言包（如 chi_sim）",
        )
    return {
        "used": True,
        "mode": ocr_mode(),
        "input_pdf": str(source_pdf),
        "output_pdf": str(output_pdf),
        "pages": after["pages"],
        "text_pages_before": before["text_pages"],
        "text_pages_after": after["text_pages"],
    }


def preprocess_if_needed(
    source_pdf: Path,
    work_dir: Path,
    *,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """按 PAPEREX_OCR 模式决定是否 OCR，返回 ``{used,path,...}``。

    ``path`` 是后续应使用的 PDF（OCR 后为副本，否则原文件）。
    """
    source_pdf = Path(source_pdf)
    work_dir = Path(work_dir)
    mode = (mode or ocr_mode()).strip().lower()
    if mode not in OCR_MODES:
        mode = DEFAULT_OCR_MODE
    info = analyze(source_pdf)
    if mode == "off":
        if info["needs_ocr"]:
            raise OcrError(
                "no_text_layer",
                "未检测到可翻译的文本层（可能是扫描版 PDF，需要先 OCR）",
                hint="设置 PAPEREX_OCR=auto/force 并安装 Tesseract，或上传带文本层的 PDF",
            )
        return {"used": False, "mode": mode, "path": source_pdf, **info}
    if mode == "force" or info["needs_ocr"]:
        if not is_available():
            raise OcrError(
                "ocr_unavailable",
                "检测到扫描版 PDF，需要 OCR，但本机未安装 Tesseract",
                hint=INSTALL_HINT,
            )
        result = run_ocr(source_pdf, work_dir / "source-ocr.pdf")
        result["path"] = work_dir / "source-ocr.pdf"
        result["needs_ocr"] = info["needs_ocr"]
        return result
    return {"used": False, "mode": mode, "path": source_pdf, **info}


def describe_failure(exc: BaseException) -> Dict[str, Any]:
    """把 OCR 异常转成 API 友好的结构化错误。"""
    if isinstance(exc, OcrError):
        return {
            "code": exc.code,
            "message": exc.message,
            "hint": exc.hint,
            "retryable": exc.retryable,
        }
    return {"code": "internal", "message": str(exc), "hint": "", "retryable": False}


__all__ = [
    "DEFAULT_OCR_MODE",
    "INSTALL_HINT",
    "OCR_MODES",
    "OcrError",
    "analyze",
    "describe_failure",
    "is_available",
    "ocr_mode",
    "preprocess_if_needed",
    "run_ocr",
    "status",
    "tessdata_dir",
    "tesseract_path",
    "tesseract_version",
]
