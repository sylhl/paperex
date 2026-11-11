# -*- coding: utf-8 -*-
"""paperex 与 super_translate-native 引擎之间的薄适配层。

翻译策略全部由内置的 ``pdf_zh_translator`` 包（完整取自
super_translate-main）承担；本模块只做宿主侧兼容：

    * 在 paperex 的字体沙盒里挑选 regular / bold 中文字体
    * 按 base_url 选择 VendorTranslator 的供应商协议
    * 把引擎的同步批量翻译包装成「分组并发 + 进度回调」
    * 把引擎的 ``TranslationIssue`` 转成旧接口的 qa 摘要
    * 字体子集化（新版 PyMuPDF 下引擎自带的 glyph 校验会误报回滚）

这里不实现任何翻译/排版策略。
"""

from __future__ import annotations

import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

try:  # PyMuPDF >= 1.24 推荐的新包名
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from pdf_zh_translator.translators import (
    CachedTranslator,
    EchoTranslator,
    ProviderAuthenticationError,
    TranslationError,
    VendorTranslator,
    cache_key,
)

# 提示词版本：修改引擎提示词逻辑时递增；参与全局缓存 bucket 计算。
PROMPT_VERSION = "2026-11-10.1"

# 缓存 bucket 依赖的引擎关键文件（都相对 pdf_zh_translator/ 目录）。
ENGINE_FINGERPRINT_FILES = (
    "pdf_layout.py",
    "page_inspector.py",
    "translators.py",
    "prompt_layout.py",
    "task_formula_layout.py",
    "corpus.py",
)


def engine_fingerprint(engine_dir: Optional[Path] = None) -> str:
    """引擎关键文件 + engine_adapter.py 的 sha256 前 16 位。

    同时用于全局译文缓存 bucket（plan §5）与 qa.json 的 engine.revision：
    引擎/适配层任何关键文件变化都会让旧缓存自然失效。
    """
    root = Path(engine_dir) if engine_dir is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in ENGINE_FINGERPRINT_FILES:
        path = root / "pdf_zh_translator" / name
        digest.update(name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    try:
        digest.update(Path(__file__).resolve().read_bytes())
    except OSError:
        digest.update(b"<missing>")
    digest.update(PROMPT_VERSION.encode("utf-8"))
    return digest.hexdigest()[:16]


def engine_info() -> Dict[str, str]:
    """health / qa.json 使用的引擎信息。"""
    try:
        from pdf_zh_translator import __version__ as engine_version
    except Exception:  # pragma: no cover - 引擎缺失时不应崩溃
        engine_version = "unknown"
    return {
        "name": "pdf_zh_translator",
        "version": str(engine_version),
        "revision": engine_fingerprint(),
    }


# ---------------------------------------------------------------------------
# 中文字体：霞鹜文楷（楷体笔锋、屏幕阅读版）正文 + 霞鹜臻楷粗标题
#   两款均为 SIL OFL 1.1，随 backend/assets/fonts 分发；找不到时回退系统字体。
# ---------------------------------------------------------------------------

BUNDLED_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

FONT_REGULAR_CANDIDATES = (
    # 用户自带字体 / 随包分发的霞鹜文楷（屏幕阅读版更清晰）
    "LXGWWenKaiGBLite-Regular.ttf",
    "LXGWWenKaiGB-Regular.ttf",
    "LXGWWenKai-Regular.ttf",
    "LXGWWenKai-Medium.ttf",
    # 次选：思源黑体 / Noto Sans SC
    "NotoSansSC-Regular.ttf",
    "NotoSansSC-Regular.otf",
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Regular.ttf",
    "SourceHanSansCN-Regular.otf",
    "NotoSerifSC-Regular.ttf",
    "SourceHanSerifSC-Regular.otf",
    # Windows 黑体
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    # Windows 衬线兜底
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simfang.ttf",
    r"C:\Windows\Fonts\simkai.ttf",
    # macOS / Linux：先黑体，再宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
FONT_BOLD_CANDIDATES = (
    # 霞鹜臻楷：霞鹜文楷的加粗衍生，保持同一笔锋
    "LXGWZhenKaiGB-Regular.ttf",
    "LXGWZhenKaiSlabGB-Regular.ttf",
    "LXGWZhenKai-Regular.ttf",
    # 再次：文楷 Medium / 思源黑体粗体
    "LXGWWenKai-Medium.ttf",
    "NotoSansSC-Bold.ttf",
    "NotoSansSC-Bold.otf",
    "SourceHanSansSC-Bold.otf",
    "SourceHanSansSC-Bold.ttf",
    "SourceHanSansCN-Bold.otf",
    "NotoSerifSC-Bold.ttf",
    "SourceHanSerifSC-Bold.otf",
    # Windows 粗黑体
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Dengb.ttf",
    # macOS / Linux
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
)


@dataclass(frozen=True)
class FontChoice:
    regular: Optional[Path]
    bold: Optional[Path]

    @property
    def label(self) -> str:
        return self.regular.name if self.regular is not None else "未找到中文字体"


def _font_loads(path: Path) -> bool:
    try:
        fitz.Font(fontfile=str(path))
        return True
    except Exception:
        return False


def _pick_font(names: Sequence[str], font_dir: Path) -> Optional[Path]:
    for name in names:
        path = Path(name)
        candidates = (
            [path]
            if path.is_absolute()
            else [font_dir / name, BUNDLED_FONT_DIR / name]
        )
        for candidate in candidates:
            if candidate.is_file() and _font_loads(candidate):
                return candidate
    return None


def discover_fonts(font_dir: Path) -> FontChoice:
    regular = _pick_font(FONT_REGULAR_CANDIDATES, font_dir)
    if regular is None:
        try:
            from pdf_zh_translator.pdf_layout import find_default_font_file

            found = find_default_font_file()
            if found is not None and _font_loads(found):
                regular = found
        except Exception:
            regular = None
    bold = _pick_font(FONT_BOLD_CANDIDATES, font_dir)
    if regular is None:
        return FontChoice(None, None)
    return FontChoice(regular, bold or regular)


# ---------------------------------------------------------------------------
# 翻译器：super_translate 的多供应商适配 + JSONL 缓存
# ---------------------------------------------------------------------------


def resolve_vendor_mode(base_url: str, model: str) -> str:
    """按端点/模型选择 super_translate 已实现的供应商协议。

    DeepSeek V4/Flash 走上游的 native deepseek 协议（带 thinking 开关与
    json_object）；旧版 deepseek-chat / deepseek-reasoner 并没有这个开关，
    改走 OpenAI 兼容协议，避免默认配置被服务端以未知字段拒绝。
    """
    host = (urlparse(base_url).hostname or "").lower()
    if "anthropic" in host:
        return "anthropic"
    if "deepseek" in host:
        model_key = (model or "").strip().lower()
        if model_key.startswith(("deepseek-v4", "deepseek-flash")):
            return "deepseek"
        return "openai-compatible"
    return "openai-compatible"


def build_vendor_translator(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    batch_size: int = 8,
    max_batch_chars: int = 2500,
    max_output_tokens: int = 8192,
    retries: int = 2,
    thinking: bool = False,
    reasoning_effort: str = "high",
    terminology: Optional[Dict[str, str]] = None,
    quality_retry: bool = False,
) -> VendorTranslator:
    """组装供应商翻译器；v1.1 起支持 thinking / reasoning_effort / 术语注入。"""
    mode = resolve_vendor_mode(base_url, model)
    provider_thinking = "enabled" if thinking else "disabled"
    return VendorTranslator(
        api_url=base_url,
        api_key=api_key or None,
        mode=mode,
        model=model or None,
        source_lang="en",
        target_lang="zh",
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        retries=max(0, int(retries)),
        deepseek_thinking=provider_thinking,
        reasoning_effort=(reasoning_effort or "high"),
        progress=False,
        terminology=terminology,
        quality_retry=bool(quality_retry),
    )


# 前端旧字段 ``quality``（fast/balanced/quality）→ 批量请求参数映射（port-spec B10）。
_BATCH_QUALITY_PARAMS: Dict[str, Dict[str, Any]] = {
    "fast": {"max_output_tokens": 3500, "thinking": False},
    "balanced": {"max_output_tokens": 8192, "thinking": False},
    "quality": {"max_output_tokens": 16384, "thinking": True, "reasoning_effort": "high"},
}


def batch_params_for_quality(quality: Optional[str]) -> Dict[str, Any]:
    """把旧前端的 ``quality`` 字段映射成批量请求参数（未知值回退 balanced）。"""
    key = (quality or "").strip().lower()
    return dict(_BATCH_QUALITY_PARAMS.get(key, _BATCH_QUALITY_PARAMS["balanced"]))


class CacheStatsTranslator:
    """统计全局缓存的命中/未命中，供 qa.json 的 cache 字段使用。

    只统计顶层 ``translate_batch`` 的请求（引擎重试阶梯内部的额外请求不计入），
    因此 hits + misses ≈ 可翻译块数，数值稳定、便于前端展示。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.hits = 0
        self.misses = 0
        self.fallback_blocks = 0
        # 本任务实际请求过的文本（用于把全局缓存导出成 per-job 缓存文件）
        self.used_texts: List[str] = []
        self._used_keys: set = set()

    # --- 透传 CachedTranslator / 引擎需要的接口 ---
    @property
    def block_types(self):
        return getattr(self._inner, "block_types", None)

    @block_types.setter
    def block_types(self, value) -> None:
        self._inner.block_types = value

    @property
    def quality_retry(self) -> bool:
        return bool(getattr(self._inner, "quality_retry", False))

    @quality_retry.setter
    def quality_retry(self, value) -> None:
        if hasattr(self._inner, "quality_retry"):
            self._inner.quality_retry = bool(value)

    @property
    def cache_file(self):
        return getattr(self._inner, "cache_file", None)

    @property
    def placeholder_fallbacks(self) -> List[str]:
        return list(getattr(self._inner, "placeholder_fallbacks", []) or [])

    def invalidate(self, texts) -> None:
        invalidate = getattr(self._inner, "invalidate", None)
        if callable(invalidate):
            invalidate(texts)

    def translate_batch(self, texts):
        texts = list(texts)
        cache = getattr(self._inner, "cache", None)
        if isinstance(cache, dict):
            for text in texts:
                if cache_key(text) in cache:
                    self.hits += 1
                else:
                    self.misses += 1
        for text in texts:
            key = cache_key(text)
            if key not in self._used_keys:
                self._used_keys.add(key)
                self.used_texts.append(text)
        self.fallback_blocks = len(self.placeholder_fallbacks)
        return self._inner.translate_batch(texts)

    def stats(self) -> Dict[str, int]:
        self.fallback_blocks = len(self.placeholder_fallbacks)
        return {
            "hits": self.hits,
            "misses": self.misses,
            "fallback_blocks": self.fallback_blocks,
        }


class ProgressTranslator:
    """把任意 pdf_zh_translator Translator 包装成「分组并发 + 进度」。

    与 super_translate Web 端 app/services/translator.py 的
    ``_ProgressTranslator`` 一致：按 group_size 切组、最多 concurrency 组
    同时请求，结果按输入顺序拼回；block_types / quality_retry / invalidate
    全部透传给真正的翻译器。
    """

    def __init__(
        self,
        inner: Any,
        progress_callback: Optional[Any] = None,
        *,
        group_size: int = 4,
        concurrency: int = 1,
    ) -> None:
        self._inner = inner
        self._callback = progress_callback
        self._group_size = max(1, group_size)
        self._concurrency = max(1, concurrency)
        self._last_ratio = 0.0

    @property
    def block_types(self):
        return getattr(self._inner, "block_types", None)

    @block_types.setter
    def block_types(self, value) -> None:
        self._inner.block_types = value

    @property
    def quality_retry(self) -> bool:
        return bool(getattr(self._inner, "quality_retry", False))

    @quality_retry.setter
    def quality_retry(self, value) -> None:
        if hasattr(self._inner, "quality_retry"):
            self._inner.quality_retry = bool(value)

    def invalidate(self, texts) -> None:
        invalidate = getattr(self._inner, "invalidate", None)
        if callable(invalidate):
            invalidate(texts)

    def translate_batch(self, texts):
        texts = list(texts)
        total = len(texts)
        groups = [
            texts[index : index + self._group_size]
            for index in range(0, total, self._group_size)
        ]
        if self._concurrency <= 1 or len(groups) <= 1:
            return self._translate_serial(groups, total)
        return self._translate_parallel(groups, total)

    def _report(self, ratio: float) -> None:
        if not self._callback:
            return
        ratio = max(0.0, min(1.0, ratio))
        if ratio <= self._last_ratio:
            return
        self._last_ratio = ratio
        try:
            self._callback(ratio)
        except Exception:
            pass

    def _translate_serial(self, groups, total):
        results: List[str] = []
        for group in groups:
            results.extend(self._inner.translate_batch(group))
            if total:
                self._report(len(results) / total)
        return results

    def _translate_parallel(self, groups, total):
        results_by_index: List[Optional[List[str]]] = [None] * len(groups)
        completed = 0
        executor = ThreadPoolExecutor(max_workers=self._concurrency)
        try:
            future_to_index = {
                executor.submit(self._inner.translate_batch, group): index
                for index, group in enumerate(groups)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                results_by_index[index] = list(future.result())
                completed += len(groups[index])
                if total:
                    self._report(completed / total)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        results: List[str] = []
        for group_result in results_by_index:
            results.extend(group_result or [])
        return results


def build_translator(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    cache_path: Optional[Path],
    progress_callback: Optional[Any],
    concurrency: int,
    dry_run: bool = False,
    use_cache: bool = True,
    thinking: bool = False,
    reasoning_effort: str = "high",
    terminology: Optional[Dict[str, str]] = None,
    quality_retry: bool = True,
    batch_size: int = 8,
    max_batch_chars: int = 2500,
    max_output_tokens: int = 8192,
    retries: int = 2,
):
    """组装与 super_translate 相同的翻译器栈：Vendor -> Cached -> Progress。

    返回值是 ``ProgressTranslator``，并额外挂两个宿主侧属性：

    * ``cache_stats``：``CacheStatsTranslator`` 或 None（统计命中/未命中/回退块）
    * ``inner_translator``：真正执行翻译的实例（用于 invalidate）

    ``use_cache=False`` 时跳过 CachedTranslator（不读也不写缓存文件）。
    """
    if dry_run:
        progress = ProgressTranslator(
            EchoTranslator(),
            progress_callback,
            concurrency=concurrency,
        )
        progress.cache_stats = None
        progress.inner_translator = None
        return progress
    vendor = build_vendor_translator(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        max_output_tokens=max_output_tokens,
        retries=retries,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        terminology=terminology,
        quality_retry=quality_retry,
    )
    if use_cache and cache_path is not None:
        inner: Any = CacheStatsTranslator(CachedTranslator(vendor, Path(cache_path)))
    else:
        inner = vendor
    progress = ProgressTranslator(
        inner,
        progress_callback,
        concurrency=concurrency,
    )
    progress.cache_stats = inner if isinstance(inner, CacheStatsTranslator) else None
    progress.inner_translator = inner
    return progress


# ---------------------------------------------------------------------------
# 译后 QA / 产物压缩
# ---------------------------------------------------------------------------


def summarize_issues(issues: Sequence[Any]) -> Dict[str, Any]:
    errors = 0
    warnings = 0
    codes: Dict[str, int] = {}
    payload: List[Dict[str, Any]] = []
    for issue in issues:
        code = getattr(issue, "code", None)
        severity = getattr(issue, "severity", "warning")
        message = getattr(issue, "message", "")
        page = getattr(issue, "page", 0)
        if isinstance(issue, dict):
            code = issue.get("code", code)
            severity = issue.get("severity", severity)
            message = issue.get("message", message)
            page = issue.get("page", page)
        code = str(code or "unknown")
        severity = str(severity or "warning")
        codes[code] = codes.get(code, 0) + 1
        if severity == "error":
            errors += 1
        elif severity == "warning":
            warnings += 1
        if len(payload) < 50:
            payload.append(
                {
                    "page": page,
                    "code": code,
                    "severity": severity,
                    "message": message,
                }
            )
    return {
        "errors": errors,
        "warnings": warnings,
        "total": len(issues),
        "codes": codes,
        "issues": payload,
    }


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _text_stats(document: Any) -> tuple:
    text = "\n".join(document[index].get_text("text") or "" for index in range(document.page_count))
    compact = re.sub(r"\s+", "", text)
    return len(_CJK_RE.findall(text)), len(compact)


def compact_output_fonts(path: Path, *, min_bytes: int = 1_500_000) -> bool:
    """对引擎产物做一次字体子集化，失败或丢字则保持原文件。

    新版 PyMuPDF 下 super_translate 的 subset_lost_glyphs() 会把正常子集
    误判为丢字并回滚，导致每份 PDF 都带上 10-20MB 的完整中文字体。
    这里用文本层回归做更直接的校验：CJK 字数不减少、总字符数不低于
    98%、文件确实变小，才替换原文件。
    """
    temp: Optional[Path] = None
    try:
        if not path.is_file() or path.stat().st_size < min_bytes:
            return False
        original_size = path.stat().st_size
        document = fitz.open(str(path))
        try:
            before_cjk, before_chars = _text_stats(document)
            if before_cjk <= 0:
                return False
            try:
                document.subset_fonts()
            except Exception:
                return False
            temp = path.with_name(".%s.subset.tmp" % path.name)
            try:
                document.save(
                    str(temp),
                    garbage=4,
                    clean=True,
                    deflate=True,
                    deflate_images=True,
                    deflate_fonts=True,
                )
            except Exception:
                try:
                    document.save(str(temp), garbage=4, deflate=True)
                except Exception:
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                    return False
        finally:
            document.close()

        check = fitz.open(str(temp))
        try:
            after_cjk, after_chars = _text_stats(check)
        finally:
            check.close()
        if (
            after_cjk >= before_cjk
            and after_chars >= int(before_chars * 0.98)
            and temp.stat().st_size < original_size
        ):
            os.replace(temp, path)
            return True
        try:
            temp.unlink()
        except OSError:
            pass
        return False
    except Exception:
        if temp is not None:
            try:
                temp.unlink()
            except OSError:
                pass
        return False


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, ProviderAuthenticationError):
        return "翻译接口鉴权失败（401/403），请检查 API Key、模型权限与 base_url"
    if isinstance(exc, TranslationError):
        return "翻译接口调用失败：%s" % exc
    return str(exc)




# 错误码 → 可操作提示（plan §2 错误码表）
_ERROR_HINTS: Dict[str, str] = {
    "auth_failed": "请检查 API Key 是否有效、是否开通该模型权限，并确认 base_url 与供应商匹配",
    "provider_unreachable": "请检查网络连接、代理设置与 base_url 是否正确",
    "provider_error": "供应商返回错误，可稍后重试或更换模型",
    "invalid_pdf": "请确认文件是完整的 PDF，必要时用阅读器重新导出",
    "encrypted_pdf": "请先用 PDF 工具解除密码保护后再上传",
    "ocr_required": "该 PDF 没有文本层（扫描版）。请安装 Tesseract OCR 后重试，或先做 OCR 生成可搜索 PDF",
    "ocr_unavailable": "本机未安装 OCR 引擎（Tesseract），无法自动识别扫描版 PDF",
    "too_large": "请拆分文档或压缩图片后再试",
    "no_text_layer": "未检测到可翻译的文本层，可能是扫描版 PDF 或纯图片文档",
    "canceled": "任务已被取消",
    "engine_error": "排版引擎处理失败，可尝试重新提交任务",
    "qa_failed": "译后自检未能完成，译文仍可正常使用",
    "interrupted": "后端进程重启导致任务中断，可重新提交（已翻译内容会命中缓存）",
    "internal": "服务内部错误，请查看后端日志",
}

_RETRYABLE_CODES = frozenset(
    {"provider_unreachable", "provider_error", "engine_error", "interrupted", "internal"}
)


def error_hint(code: str) -> str:
    return _ERROR_HINTS.get(code, _ERROR_HINTS["internal"])


def classify_error(exc: BaseException) -> Dict[str, Any]:
    """把任意异常归类成 plan §2 的结构化错误 ``{code,message,hint,retryable}``。

    优先级：宿主/引擎自定义异常（带 ``code`` 属性）→ 供应商异常 → 启发式。
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        message = str(getattr(exc, "message", None) or exc)
        hint = str(getattr(exc, "hint", None) or error_hint(code))
        retryable = bool(getattr(exc, "retryable", code in _RETRYABLE_CODES))
        return {
            "code": code,
            "message": message,
            "hint": hint,
            "retryable": retryable,
        }
    if isinstance(exc, ProviderAuthenticationError):
        return {
            "code": "auth_failed",
            "message": "翻译接口鉴权失败（401/403）",
            "hint": error_hint("auth_failed"),
            "retryable": False,
        }
    if isinstance(exc, TranslationError):
        text = str(exc)
        lowered = text.lower()
        unreachable = any(
            marker in lowered
            for marker in ("connect", "timed out", "timeout", "unreachable", "network")
        )
        code = "provider_unreachable" if unreachable else "provider_error"
        return {
            "code": code,
            "message": f"翻译接口调用失败：{text}"[:500],
            "hint": error_hint(code),
            "retryable": True,
        }

    text = str(exc)
    lowered = text.lower()
    if "加密" in text or "password" in lowered or "encrypted" in lowered:
        code = "encrypted_pdf"
    elif "文本层" in text or "scanned" in lowered or "no text" in lowered:
        code = "no_text_layer"
    elif "过大" in text or "too large" in lowered or "超过单次上限" in text:
        code = "too_large"
    elif isinstance(exc, FileNotFoundError):
        code = "invalid_pdf"
    elif isinstance(exc, (ValueError, RuntimeError)) and "pdf" in lowered:
        code = "invalid_pdf"
    else:
        code = "internal"
    return {
        "code": code,
        "message": text[:500] or "未知错误",
        "hint": error_hint(code),
        "retryable": code in _RETRYABLE_CODES,
    }


__all__ = [
    "BUNDLED_FONT_DIR",
    "CacheStatsTranslator",
    "ENGINE_FINGERPRINT_FILES",
    "FontChoice",
    "PROMPT_VERSION",
    "ProgressTranslator",
    "batch_params_for_quality",
    "build_translator",
    "build_vendor_translator",
    "classify_error",
    "compact_output_fonts",
    "describe_error",
    "discover_fonts",
    "engine_fingerprint",
    "engine_info",
    "error_hint",
    "resolve_vendor_mode",
    "summarize_issues",
]


