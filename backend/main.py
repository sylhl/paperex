# -*- coding: utf-8 -*-
"""
paperex · 后端服务 (v1.1)
================================================================
本地优先的学术论文「原位重排」翻译引擎 + 候选制质量循环：

    本地 PDF -> OCR 预处理（可选） -> super_translate-native 结构分析
             -> 保护区 + 可逆占位符 -> LLM 批量翻译（术语注入 / 占位符校验 / 失败阶梯）
             -> 擦除原文 -> 中文原位回填 -> 隔离 QA（verify + inspect）
             -> 候选修复循环（重排版 / 重译，严格变好才接受 -> 否则回滚快照）
             -> 新 PDF + qa.json

v1.1 主要新增
----------------------------------------------------------------
1. 文件型任务状态机（``%APPDATA%/paperex/jobs/<job_id>/job.json``）+ 心跳 + 重启恢复
2. 协作式取消：``POST /api/jobs/{id}/cancel``（排队任务立即取消，运行任务在进度/阶段边界生效）
3. 隔离 QA：``qa_worker.py`` 独立进程跑 verify/inspect，主进程只合并结果，QA 崩溃不拖垮任务
4. 质量循环：``standard`` 最多 1 轮修复、``strict`` 最多 ``PAPEREX_QA_MAX_PASSES`` 轮
5. 跨任务内容寻址翻译缓存（``%APPDATA%/paperex/cache/translation/<bucket>.jsonl``）
6. 模型目录 / 供应商探活 / 术语 API / 扩展 health / 下载头 / 缓存清理
7. 向后兼容：旧字段（status/progress/result.qa 等）全部保留，旧前端不需要改

启动：
    uvicorn main:app --host 127.0.0.1 --port 8756
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

try:  # PyMuPDF >= 1.24 推荐的新包名
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from pdf_zh_translator import __version__ as ENGINE_VERSION
from pdf_zh_translator.corpus import _load_corpus, corpus_stats
from pdf_zh_translator.pdf_layout import prepare_translation_units, translate_pdf
from pdf_zh_translator.translators import cache_key

import cache as global_cache
import ocr as ocr_module
import providers as providers_module
import qa as qa_module
import qa_loop as qa_loop_module
import quality as quality_module
from engine_adapter import (
    PROMPT_VERSION,
    batch_params_for_quality,
    build_translator,
    classify_error,
    compact_output_fonts,
    discover_fonts,
    engine_fingerprint,
    engine_info,
    resolve_vendor_mode,
)
from jobs import (
    STAGE_LABELS,
    JobCanceled,
    JobError,
    JobNotFound,
    JobStore,
    can_cancel as job_can_cancel,
    is_terminal,
    max_attempts_default,
    resume_enabled,
    stage_for_status,
)


def _ensure_utf8_stdio() -> None:
    """Keep startup logs from crashing on non-UTF-8 code pages (e.g. cp1252 CI)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


_ensure_utf8_stdio()


# =================================================================
# 0. 常量 / 目录
# =================================================================

APP_NAME = "paperex"
APP_VERSION = "1.2.0"
HOST = "127.0.0.1"
PORT = 8756

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

MAX_UPLOAD_BYTES = 120 * 1024 * 1024          # 单文件上限 120MB
MAX_TRANSLATE_CHARS = 400_000                 # 单篇论文可翻译字符上限（防跑飞）
TRANSLATE_CONCURRENCY = 6                     # 并发批次（每批内部还有重试阶梯）
TRANSLATE_TIMEOUT = 150.0
MIN_FONT_SIZE = 6.0
FONT_SCALE = 0.90
JOB_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")   # 任务 ID 只可能是 uuid hex
HISTORY_META = "meta.json"                    # 每个 output/<job_id>/ 下的历史记录
QA_REPORT = "qa.json"
SNAPSHOT_DIR = ".snapshots"
CANDIDATE_DIR = ".candidates"
# 图像/表格/算法/参考文献内部文字保持原文，避免中文压住图表；图注仍翻译。
PRESERVE_GRAPHICS_TEXT = True
# 每轮重排版修复使用的 font_scale（缓存命中，不产生 API 费用）
LAYOUT_SCALES = (FONT_SCALE, 0.86, 0.82, 0.80)
LEGACY_QUALITY_VALUES = ("fast", "balanced", "quality")


def _appdata_root() -> Path:
    """%APPDATA%/paperex（非 Windows 平台做安全回退）。"""
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME


def _legacy_appdata_root() -> Path:
    """旧版 PaperReflow 的沙盒目录，首次启动时迁移到 paperex。"""
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "PaperReflow"


def _merge_tree(source: Path, target: Path) -> None:
    """把旧沙盒里的字体 / 输出 / 缓存合并到新目录，不覆盖已有文件。"""
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if destination.exists():
            if item.is_dir() and destination.is_dir():
                _merge_tree(item, destination)
            continue
        try:
            shutil.move(str(item), str(destination))
        except OSError:
            try:
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
            except OSError:
                pass


def _migrate_legacy_appdata(target: Path) -> None:
    legacy = _legacy_appdata_root()
    if not legacy.is_dir():
        return
    try:
        _merge_tree(legacy, target)
    except OSError:
        pass


BASE_DIR: Path = _appdata_root()
_migrate_legacy_appdata(BASE_DIR)
CACHE_DIR: Path = BASE_DIR / "cache"
OUTPUT_DIR: Path = BASE_DIR / "output"
JOBS_DIR: Path = BASE_DIR / "jobs"
FONT_DIR: Path = BASE_DIR / "fonts"

for _d in (BASE_DIR, CACHE_DIR, OUTPUT_DIR, JOBS_DIR, FONT_DIR):
    os.makedirs(_d, exist_ok=True)  # 硬性要求：自动创建本地沙盒目录


# =================================================================
# 1. 安全工具：文件名清洗 + 路径穿越防护
# =================================================================

_UNSAFE_NAME_RE = re.compile(r"[^\w\-. ()\u4e00-\u9fff]+", re.UNICODE)


def sanitize_filename(name: str, fallback: str = "document.pdf") -> str:
    """清洗外部传入的文件名：去掉目录部分、控制字符、危险符号。"""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "").strip().strip(".")
    name = _UNSAFE_NAME_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    if not name:
        name = fallback
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + dot + ext) if dot else name[:120]
    return name


def ensure_inside(path: Path, root: Path) -> Path:
    """解析真实路径，并保证它位于 root 之内（阻断 ../ 与符号链接穿越）。"""
    real = Path(os.path.realpath(str(path)))
    root_real = Path(os.path.realpath(str(root)))
    try:
        if os.path.commonpath([str(real), str(root_real)]) != str(root_real):
            raise ValueError("path escape")
    except ValueError:
        raise HTTPException(status_code=400, detail="非法的文件路径")
    return real


def rel_to_base(path: Path) -> str:
    return str(Path(path).relative_to(BASE_DIR)).replace("\\", "/")


# =================================================================
# 2. 中文字体（含粗体）解析
# =================================================================

FONT_CHOICE = discover_fonts(FONT_DIR)
FONT_LABEL = FONT_CHOICE.label
FONT_PATH = str(FONT_CHOICE.regular) if FONT_CHOICE.regular is not None else None


# =================================================================
# 3. 翻译接口配置 / 请求模型
# =================================================================

class TranslateConfig(BaseModel):
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = TRANSLATE_TIMEOUT


class ConvertRequest(BaseModel):
    pdf_path: str = Field(..., description="Tauri 文件选择器返回的本地绝对路径")
    api_key: Optional[str] = Field(None, description="翻译 API Key（留空则读环境变量）")
    base_url: str = Field(DEFAULT_BASE_URL, description="OpenAI 兼容 / DeepSeek / Anthropic base url")
    model: str = Field(DEFAULT_MODEL)
    target_lang: str = Field("简体中文")
    # ---- v1.1 质量循环 / 供应商参数（全部可选，旧前端不带也能工作）----
    quality_mode: Optional[str] = Field(
        None, description="off | fast | standard | strict（缺省按 PAPEREX_QA_MODE / PAPEREX_POST_QA / standard）"
    )
    thinking: Optional[bool] = Field(None, description="是否开启供应商 reasoning")
    reasoning_effort: Optional[str] = Field(None, description="low | high | max")
    use_cache: Optional[bool] = Field(True, description="是否使用跨任务全局翻译缓存")
    max_attempts: Optional[int] = Field(None, description="任务最大重试次数（1-8）")
    quality: Optional[str] = Field(None, description="旧字段：fast | balanced | quality")


class TestConnectionRequest(BaseModel):
    base_url: str = Field(DEFAULT_BASE_URL)
    model: str = Field(DEFAULT_MODEL)
    api_key: Optional[str] = Field(None)


def _dry_run() -> bool:
    value = os.environ.get("PAPEREX_DRYRUN")
    if value is None:
        value = os.environ.get("PAPERREFLOW_DRYRUN")
    return (value or "").strip() in {"1", "true", "yes"}


_KEY_ENVS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "PAPEREX_API_KEY",
    "PAPERREFLOW_API_KEY",
)


def _env_api_key() -> str:
    for env in _KEY_ENVS:
        value = (os.environ.get(env) or "").strip()
        if value:
            return value
    return ""


def _resolve_api_key(req: Optional[ConvertRequest] = None) -> str:
    if req is not None:
        key = (req.api_key or "").strip()
        if key:
            return key
    return _env_api_key()


def _default_quality_mode() -> str:
    """默认质量档：PAPEREX_QA_MODE 优先，其次兼容旧 PAPEREX_POST_QA 开关。"""
    env_mode = (os.environ.get("PAPEREX_QA_MODE") or "").strip().lower()
    if env_mode:
        if env_mode in ("single", "single-pass", "once"):
            return "standard"
        return quality_module.normalize_mode(env_mode)
    post_qa = os.environ.get("PAPEREX_POST_QA")
    if post_qa is not None and post_qa.strip().lower() in {"0", "false", "no", "off"}:
        return "off"
    return quality_module.DEFAULT_QA_MODE


def _quality_mode_for(req: ConvertRequest) -> str:
    if req.quality_mode:
        value = req.quality_mode.strip().lower()
        if value not in quality_module.QA_MODES:
            raise ApiError(
                400,
                f"不支持的 quality_mode：{req.quality_mode}（可选 off/fast/standard/strict）",
                code="invalid_request",
                hint="quality_mode 只能是 off、fast、standard 或 strict",
            )
        return value
    return _default_quality_mode()


def _translator_options(req: ConvertRequest, mode: str) -> Dict[str, Any]:
    """把请求里的供应商参数解析成 build_translator 的 kwargs。"""
    opts: Dict[str, Any] = {
        "thinking": False,
        "reasoning_effort": "high",
        "batch_size": 8,
        "max_batch_chars": int(os.environ.get("PAPEREX_MAX_BATCH_CHARS") or 2500),
        "max_output_tokens": 8192,
        "retries": 2,
    }
    legacy = (req.quality or "").strip().lower()
    if legacy in LEGACY_QUALITY_VALUES:
        opts.update(batch_params_for_quality(legacy))
    if req.thinking is not None:
        opts["thinking"] = bool(req.thinking)
    if req.reasoning_effort:
        effort = req.reasoning_effort.strip().lower()
        opts["reasoning_effort"] = effort if effort in {"low", "high", "max"} else "high"
    if mode == "strict" and req.quality is None and req.thinking is None:
        strict_opts = batch_params_for_quality("quality")
        opts["thinking"] = bool(strict_opts.get("thinking", False))
        opts["reasoning_effort"] = str(strict_opts.get("reasoning_effort") or "high")
        opts["max_output_tokens"] = int(strict_opts.get("max_output_tokens") or 16384)
    return opts


# =================================================================
# 4. 结构化错误
# =================================================================

class ApiError(Exception):
    """HTTP 层的结构化错误：同时输出 detail 字符串与 error 对象。"""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str = "internal",
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = str(message)
        self.code = code
        self.hint = hint
        self.retryable = bool(retryable)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


class _Cancelled(BaseException):
    """引擎工作线程内的取消信号。

    ``ProgressTranslator._report`` 会 ``except Exception: pass`` 吞掉回调异常，
    因此这里必须继承 ``BaseException`` 才能中断 ``translate_pdf``。
    """


# =================================================================
# 5. 任务状态机 / 取消
# =================================================================

STORE = JobStore(JOBS_DIR)
_BG_TASKS: set = set()                     # 持有强引用，避免后台任务被 GC 回收
_JOB_TASKS: Dict[str, asyncio.Task] = {}   # job_id -> asyncio.Task（排队取消用）
_ENGINE_LOCK = asyncio.Lock()              # 引擎含文档级全局状态，任务串行执行


def _track_task(job_id: str, task: asyncio.Task) -> None:
    _BG_TASKS.add(task)
    _JOB_TASKS[job_id] = task

    def _cleanup(finished: asyncio.Task) -> None:
        _BG_TASKS.discard(finished)
        if _JOB_TASKS.get(job_id) is finished:
            _JOB_TASKS.pop(job_id, None)

    task.add_done_callback(_cleanup)


def _safe_update(job_id: str, **fields: Any) -> None:
    try:
        STORE.update(job_id, **fields)
    except JobNotFound:  # 任务被并发删除：忽略即可
        pass


def _raise_if_cancelled(job_id: str) -> None:
    if STORE.is_cancelled(job_id):
        raise JobCanceled()


def _raise_if_cancelled_engine(job_id: str) -> None:
    """翻译线程里的取消检查：必须抛 BaseException 才能穿透进度回调的吞异常逻辑。"""
    if STORE.is_cancelled(job_id):
        raise _Cancelled()


def _classify_failure(exc: BaseException) -> Dict[str, Any]:
    if isinstance(exc, ocr_module.OcrError):
        return ocr_module.describe_failure(exc)
    if isinstance(exc, JobError):
        return exc.as_detail()
    try:
        detail = classify_error(exc)
    except Exception:  # pragma: no cover - 分类器自身异常时兜底
        detail = {"code": "internal", "message": str(exc), "hint": "", "retryable": False}
    if not isinstance(detail, dict):
        detail = {"code": "internal", "message": str(exc), "hint": "", "retryable": False}
    detail.setdefault("message", str(exc) or "未知错误")
    detail.setdefault("code", "internal")
    detail.setdefault("hint", "")
    detail.setdefault("retryable", False)
    return detail


def _empty_timings() -> Dict[str, Any]:
    return {"translate": None, "qa": None, "repair": None, "total": None}


def _mark_failed(job_id: str, out_path: Path, detail: Dict[str, Any]) -> None:
    try:
        out_path.unlink(missing_ok=True)
    except OSError:
        pass
    message = str(detail.get("message") or "转换失败")
    _safe_update(
        job_id,
        force=True,
        status="error",
        stage="failed",
        progress=1.0,
        message="转换失败",
        error=message,
        error_detail=detail,
        result=None,
        started_at=None,
    )
    print(f"[paperex] job {job_id} failed: {detail.get('code')} - {message}")


def _mark_cancelled(job_id: str, job_dir: Path, out_path: Path) -> None:
    try:
        out_path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        shutil.rmtree(job_dir / CANDIDATE_DIR, ignore_errors=True)
    except OSError:
        pass
    _safe_update(
        job_id,
        force=True,
        status="cancelled",
        stage="canceled",
        progress=1.0,
        message="任务已取消",
        error="任务已取消",
        error_detail={"code": "canceled", "message": "任务已取消", "hint": "", "retryable": False},
        result=None,
        started_at=None,
    )


# =================================================================
# 6. 源文件解析 / 翻译 / QA 辅助
# =================================================================

def _analyze_source(path: Path) -> Dict[str, Any]:
    """用引擎自己的提取/分类/保护区流程统计可翻译单元。"""
    document = fitz.open(str(path))
    try:
        if document.needs_pass:
            raise JobError("该 PDF 已加密，请先解除密码保护", code="encrypted_pdf", hint="解除密码保护后重新提交")
        units, _gutters, skipped = prepare_translation_units(
            document, preserve_graphics_text=PRESERVE_GRAPHICS_TEXT
        )
        chars = sum(len(prompt) for _block, prompt, _mapping in units)
        return {
            "pages": document.page_count,
            "translatable": len(units),
            "chars": chars,
            "skipped": skipped,
        }
    finally:
        document.close()


def _warning_count(warnings, pattern: str) -> int:
    total = 0
    for warning in warnings:
        match = re.search(pattern, warning)
        if match:
            total += int(match.group(1))
    return total


def _translate_pdf_to(
    source_pdf: Path,
    target_pdf: Path,
    translator: Any,
    *,
    font_scale: float,
    skip_overflow: bool,
) -> Any:
    """调用引擎的整篇翻译 + 原位擦写。"""
    return translate_pdf(
        source_pdf,
        target_pdf,
        translator,
        font_file=FONT_CHOICE.regular,
        bold_font_file=FONT_CHOICE.bold,
        min_font_size=MIN_FONT_SIZE,
        font_scale=font_scale,
        margin=0.8,
        preserve_graphics_text=PRESERVE_GRAPHICS_TEXT,
        skip_overflow=skip_overflow,
        harmonize_body_font_size=True,
    )


def _layout_scale_for_round(round_no: int) -> float:
    index = max(0, min(int(round_no), len(LAYOUT_SCALES) - 1))
    return LAYOUT_SCALES[index]


def _prompts_for_pages(source_pdf: Path, pages: List[Any]) -> List[str]:
    """收集指定页的可翻译 prompt，供重译前 invalidate 缓存。"""
    wanted = {int(page) for page in pages if isinstance(page, int) or str(page).isdigit()}
    if not wanted:
        return []
    try:
        document = fitz.open(str(source_pdf))
    except Exception:
        return []
    try:
        units, _gutters, _skipped = prepare_translation_units(
            document, preserve_graphics_text=PRESERVE_GRAPHICS_TEXT
        )
    except Exception:
        return []
    finally:
        document.close()
    prompts: List[str] = []
    for block, prompt, _mapping in units:
        page_index = int(getattr(block, "page_index", 0) or 0)
        if page_index in wanted or (page_index + 1) in wanted:
            prompts.append(prompt)
    return prompts


def _qa_evaluate(original_pdf: Path, translated_pdf: Path, mode: str, scratch_dir: Path) -> Any:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return qa_module.evaluate(
        Path(original_pdf),
        Path(translated_pdf),
        mode=mode,
        timeout=qa_module.qa_timeout_s(),
        scratch_dir=Path(scratch_dir),
    )


def _skipped_qa() -> Any:
    return qa_module.QaResult(
        issues=[],
        status="skipped",
        score=(0, 0),
        fingerprint=quality_module.issue_fingerprint([]),
        timings_ms={"qa": 0},
    )


def _snapshot_writer(job_id: str, job_dir: Path, mode: str):
    def _write(round_no: int, pdf_path: Path, qa_result: Any) -> None:
        target = job_dir / SNAPSHOT_DIR / f"round-{round_no}"
        target.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(pdf_path, target / "translated.pdf")
        except OSError:
            return
        try:
            snapshot_report = qa_module.build_report(
                job_id=job_id,
                mode=mode,
                result=qa_result,
                passes_run=int(round_no),
                repair_attempted=True,
                timings_ms={
                    "translate": 0,
                    "qa": int((getattr(qa_result, "timings_ms", {}) or {}).get("qa") or 0),
                    "repair": 0,
                    "total": 0,
                },
            )
            qa_module.write_report(target / QA_REPORT, snapshot_report)
        except Exception:  # 快照失败不影响主流程
            return

    return _write


def _export_job_cache(cache_path: Optional[Path], export_path: Path, translator: Any) -> None:
    """把全局缓存里本任务实际用到的条目导出成 per-job 缓存文件（可导出/排查）。"""
    stats = getattr(translator, "cache_stats", None)
    used_texts = list(getattr(stats, "used_texts", None) or [])
    if not used_texts or cache_path is None:
        return
    source_file = Path(cache_path)
    if source_file == Path(export_path) or not source_file.is_file():
        return
    entries: Dict[str, Dict[str, Any]] = {}
    try:
        with open(source_file, encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("key"):
                    entries[str(item["key"])] = item
    except OSError:
        return
    used_keys = [cache_key(text) for text in used_texts]
    lines = []
    for key in used_keys:
        item = entries.get(key)
        if not item:
            continue
        lines.append(
            json.dumps(
                {
                    "key": key,
                    "source": item.get("source", ""),
                    "translation": item.get("translation", ""),
                },
                ensure_ascii=False,
            )
        )
    if not lines:
        return
    temp = Path(export_path).with_suffix(".jsonl.tmp")
    try:
        temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temp, export_path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


# =================================================================
# 7. 全局缓存 / 术语
# =================================================================

_TERMS_FP_CACHE: Optional[str] = None
_TERMS_CACHE: Optional[Dict[str, Any]] = None


def _terms_fingerprint() -> str:
    global _TERMS_FP_CACHE
    if _TERMS_FP_CACHE is None:
        try:
            _TERMS_FP_CACHE = global_cache.terms_fingerprint()
        except Exception:  # 术语库异常不应阻断翻译
            _TERMS_FP_CACHE = ""
    return _TERMS_FP_CACHE


def _terminology_data() -> Dict[str, Any]:
    """读取术语库并展开为 API 需要的结构（一次性缓存）。"""
    global _TERMS_CACHE
    if _TERMS_CACHE is not None:
        return _TERMS_CACHE
    terms: List[Dict[str, Any]] = []
    categories: List[Dict[str, Any]] = []
    try:
        corpus = _load_corpus()
    except Exception:  # pragma: no cover - 术语库损坏时降级
        corpus = {}
    for category, mapping in corpus.items():
        if not isinstance(mapping, dict):
            continue
        categories.append({"name": str(category), "count": len(mapping)})
        for en, zh in mapping.items():
            terms.append({"en": str(en), "zh": str(zh), "category": str(category), "note": ""})
    categories.sort(key=lambda item: (-int(item["count"]), str(item["name"])))
    _TERMS_CACHE = {"total": len(terms), "categories": categories, "terms": terms}
    return _TERMS_CACHE


def _bucket_for(req: ConvertRequest, engine_revision: str) -> str:
    return global_cache.bucket_for(
        protocol=resolve_vendor_mode(req.base_url, req.model),
        api_url=req.base_url,
        model=req.model,
        engine_revision=engine_revision,
        prompt_version=PROMPT_VERSION,
        terms_fp=_terms_fingerprint(),
    )


# =================================================================
# 8. 任务执行：翻译 -> QA -> 候选修复循环 -> 产物
# =================================================================

async def run_job(job_id: str, req: ConvertRequest, src: Path, api_key: str) -> None:
    started = time.time()
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    file_name = _job_file_name(job_id, src)
    stem = sanitize_filename(file_name, "paper.pdf")
    stem = stem[:-4] if stem.lower().endswith(".pdf") else stem
    out_path = job_dir / f"{stem}.zh.pdf"

    try:
        async with _ENGINE_LOCK:
            await _run_pipeline(job_id, req, src, api_key, job_dir, out_path, started)
    except (asyncio.CancelledError, _Cancelled, JobCanceled):
        _mark_cancelled(job_id, job_dir, out_path)
    except JobError as exc:
        _mark_failed(job_id, out_path, exc.as_detail())
    except Exception as exc:  # noqa: BLE001 - 所有失败都要结构化落盘
        _mark_failed(job_id, out_path, _classify_failure(exc))


async def _run_pipeline(
    job_id: str,
    req: ConvertRequest,
    src: Path,
    api_key: str,
    job_dir: Path,
    out_path: Path,
    started: float,
) -> None:
    mode = _quality_mode_for(req)
    max_rounds = quality_module.max_rounds_for_mode(mode)
    _safe_update(
        job_id,
        force=True,
        status="running",
        stage="analyzing",
        progress=0.03,
        message="正在解析 PDF 文本层、图表与公式…",
        started_at=time.time(),
        quality={
            "mode": mode,
            # max = 基线一轮 + 允许的修复轮数（off 不跑 QA，故为 0）
            "round": {"current": 0, "max": 0 if mode == "off" else int(max_rounds) + 1, "status": "running"},
        },
    )
    _raise_if_cancelled(job_id)

    # ---------- 1) OCR 预处理（PAPEREX_OCR=off/auto/force） ----------
    ocr_info = await asyncio.to_thread(ocr_module.preprocess_if_needed, src, job_dir)
    effective_src = Path(ocr_info.get("path") or src)
    _raise_if_cancelled(job_id)

    # ---------- 2) 结构分析（引擎提取链路） ----------
    stats = await asyncio.to_thread(_analyze_source, effective_src)
    translatable = int(stats["translatable"])
    pages_total = int(stats["pages"])
    if translatable == 0:
        raise JobError(
            "未检测到可翻译的文本层（可能是扫描版 PDF，需要先 OCR）",
            code="no_text_layer",
            hint=ocr_module.INSTALL_HINT,
        )
    if stats["chars"] > MAX_TRANSLATE_CHARS:
        raise JobError(
            f"文档文本量过大（{stats['chars']} 字符），超过单次上限 {MAX_TRANSLATE_CHARS}，请拆分后重试",
            code="too_large",
            hint="把论文拆分后分批提交",
        )
    _safe_update(
        job_id,
        progress=0.10,
        message=f"已识别 {translatable} 个可翻译块（保护区 {stats['skipped']} 块），准备调用翻译接口…",
        pages={"total": pages_total, "done": 0},
    )
    _raise_if_cancelled(job_id)

    # ---------- 3) 翻译器（全局缓存 + 供应商参数透传） ----------
    engine_rev = engine_fingerprint()
    protocol = resolve_vendor_mode(req.base_url, req.model)
    options = _translator_options(req, mode)
    use_cache = True if req.use_cache is None else bool(req.use_cache)
    if use_cache:
        cache_path: Optional[Path] = global_cache.cache_file(_bucket_for(req, engine_rev))
        global_cache.ensure_cache_dir()
    else:
        cache_path = job_dir / "translation-cache.jsonl"
    export_path = job_dir / "translation-cache.jsonl"

    def on_progress(ratio: float) -> None:
        _raise_if_cancelled_engine(job_id)
        value = max(0.0, min(1.0, float(ratio)))
        # pages_done 语义 = 已完成页数（按比例估算，终态回填为 pages_total）
        done_pages = min(pages_total, int(round(value * pages_total))) if pages_total else 0
        done_blocks = int(round(value * translatable))
        _safe_update(
            job_id,
            progress=round(0.10 + 0.62 * value, 4),
            message=f"正在翻译：{done_blocks}/{translatable} 块",
            pages={"total": pages_total, "done": done_pages},
            blocks={"total": translatable, "done": done_blocks},
        )

    if FONT_CHOICE.regular is None:
        raise JobError(
            "未找到可嵌入的中文字体",
            code="engine_error",
            hint="把 .ttf 字体放入 %APPDATA%/paperex/fonts/ 后重试",
        )

    translator = build_translator(
        base_url=req.base_url,
        api_key=api_key,
        model=req.model,
        timeout=TRANSLATE_TIMEOUT,
        cache_path=cache_path,
        progress_callback=on_progress,
        concurrency=TRANSLATE_CONCURRENCY,
        dry_run=_dry_run(),
        use_cache=use_cache,
        thinking=bool(options.get("thinking")),
        reasoning_effort=str(options.get("reasoning_effort") or "high"),
        terminology=None,
        quality_retry=True,
        batch_size=int(options.get("batch_size") or 8),
        max_batch_chars=int(options.get("max_batch_chars") or 2500),
        max_output_tokens=int(options.get("max_output_tokens") or 8192),
        retries=int(options.get("retries") or 2),
    )

    translate_started = time.time()
    _safe_update(job_id, stage="translating", progress=0.11, message="正在翻译并擦除英文、按原坐标回填中文…")
    report = await asyncio.to_thread(
        _translate_pdf_to,
        effective_src,
        out_path,
        translator,
        font_scale=FONT_SCALE,
        skip_overflow=False,
    )
    translate_ms = int((time.time() - translate_started) * 1000)
    _raise_if_cancelled(job_id)
    _safe_update(
        job_id,
        progress=0.74,
        message="译文已生成，正在收尾…",
        pages={"total": pages_total, "done": pages_total},
        blocks={"total": translatable, "done": translatable},
    )

    if not PRESERVE_GRAPHICS_TEXT:
        await asyncio.to_thread(compact_output_fonts, out_path)
    await asyncio.to_thread(_export_job_cache, cache_path, export_path, translator)

    # ---------- 4) 隔离 QA + 候选修复循环 ----------
    qa_dir = job_dir / ".qa"
    qa_ms = 0
    repair_ms = 0
    loop_outcome: Optional[Dict[str, Any]] = None
    initial_qa: Any = None
    best_qa: Any = None
    passes_run = 1
    qa_report: Optional[Dict[str, Any]] = None
    report_url = f"/api/jobs/{job_id}/qa"
    if mode == "off":
        initial_qa = _skipped_qa()
        report_url = f"/api/jobs/{job_id}/qa"
        qa_report = qa_module.build_report(
            job_id=job_id,
            mode=mode,
            result=initial_qa,
            passes_run=0,
            engine=engine_info(),
            config_fingerprint=_config_fingerprint(engine_rev, protocol, req, mode),
            terminology=_terminology_payload(),
            cache_stats=_cache_stats(translator),
            timings_ms=_timings_payload(translate_ms, 0, 0, started),
        )
    else:
        qa_scratch = qa_dir
        qa_scratch.mkdir(parents=True, exist_ok=True)
        _safe_update(job_id, stage="qa", progress=0.76, message="正在执行隔离质量检查（verify + inspect）…")
        initial_qa = await asyncio.to_thread(_qa_evaluate, effective_src, out_path, mode, qa_scratch)
        qa_ms += int((initial_qa.timings_ms or {}).get("qa") or 0)
        _raise_if_cancelled(job_id)
        initial_status = str(getattr(initial_qa, "status", ""))
        _safe_update(
            job_id,
            progress=0.82,
            message=f"质量检查完成（{initial_status}），正在评估是否需要修复…",
        )

        loop_outcome = None
        if max_rounds > 0:
            loop_outcome = await _run_quality_loop(
                job_id=job_id,
                req=req,
                mode=mode,
                max_rounds=max_rounds,
                effective_src=effective_src,
                out_path=out_path,
                initial_qa=initial_qa,
                translator=translator,
                job_dir=job_dir,
                started=started,
                state={"issues": list(initial_qa.issues)},
                stats={"qa_ms": qa_ms, "repair_ms": 0},
            )
        best_qa = initial_qa
        if loop_outcome is not None:
            qa_ms = int(loop_outcome["timings"]["qa"])
            repair_ms = int(loop_outcome["timings"]["repair"])
            passes_run = int(loop_outcome["passes_run"]) + 1
            best_qa = loop_outcome["best_qa"] or initial_qa
            best_pdf = Path(loop_outcome["best_pdf"])
            if best_pdf != out_path and best_pdf.is_file():
                shutil.copy2(best_pdf, out_path)
                if not PRESERVE_GRAPHICS_TEXT:
                    await asyncio.to_thread(compact_output_fonts, out_path)
        _raise_if_cancelled(job_id)

        timings_ms = _timings_payload(translate_ms, qa_ms, repair_ms, started)
        qa_report = qa_module.build_report(
            job_id=job_id,
            mode=mode,
            result=best_qa,
            passes_run=passes_run,
            repair_attempted=bool(loop_outcome and loop_outcome.get("repair_attempted")),
            pass_history=(loop_outcome or {}).get("pass_history"),
            engine=engine_info(),
            config_fingerprint=_config_fingerprint(engine_rev, protocol, req, mode),
            terminology=_terminology_payload(),
            cache_stats=_cache_stats(translator),
            timings_ms=timings_ms,
        )
        report_url = f"/api/jobs/{job_id}/qa"
        if loop_outcome:
            qa_report["loop_status"] = loop_outcome.get("status")
            qa_report["stopped_reason"] = loop_outcome.get("stopped_reason")

    _raise_if_cancelled(job_id)
    if best_qa is None:
        best_qa = initial_qa
    qa_report["report_url"] = report_url
    qa_report["quality_mode"] = mode
    qa_module.write_report(job_dir / QA_REPORT, qa_report)
    qa_summary = qa_module.summarize(qa_report)

    # ---------- 5) 组装结果 / 写历史 ----------
    kept_verbatim = _warning_count(report.warnings, r"Kept (\d+) block\(s\)")
    missing_placeholders = _warning_count(report.warnings, r"dropped (\d+) placeholder\(s\)")
    written = max(0, int(report.translated_blocks) - kept_verbatim)
    total_ms = int((time.time() - started) * 1000)
    timings_ms = _timings_payload(translate_ms, qa_ms, repair_ms, started)
    legacy_qa = quality_module.summarize(best_qa.issues)
    legacy_qa.update(
        {
            "status": getattr(best_qa, "status", None),
            "skipped": mode == "off",
            "qa_failed": bool(getattr(best_qa, "qa_failed", False)),
            "report_url": report_url,
        }
    )
    quality_round = {
        "current": int(passes_run if mode != "off" else 0),
        "max": int(max_rounds + (1 if mode != "off" else 0)),
        "status": "done" if mode != "off" else "skipped",
    }
    result: Dict[str, Any] = {
        "original_url": f"/api/files/{rel_to_base(src)}",
        "translated_url": f"/api/files/{rel_to_base(out_path)}",
        "output_path": str(out_path),           # 前端/用户可直接打开的本地绝对路径
        "pages": int(report.page_count),
        "blocks": translatable,
        "translated_blocks": written,
        "skipped_blocks": int(report.skipped_blocks),
        "failed_blocks": max(0, translatable - written),
        "font": FONT_LABEL,
        "elapsed_ms": total_ms,
        "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
        # 以下为兼容旧前端 / 2.x 扩展字段
        "engine": f"pdf_zh_translator {ENGINE_VERSION}",
        "engine_revision": engine_rev,
        "merged_paragraphs": 0,
        "skipped_placeholders": missing_placeholders,
        "qa": legacy_qa,
        "qa_summary": qa_summary,
        "quality_mode": mode,
        "quality_round": quality_round,
        "engine_warnings": list(report.warnings[:50]),
        "cache": _cache_stats(translator),
        "model": req.model,
        "ocr": {
            "used": bool(ocr_info.get("used")),
            "mode": ocr_info.get("mode"),
            "pages": ocr_info.get("pages"),
            "text_pages": ocr_info.get("text_pages"),
        },
    }

    _safe_update(
        job_id,
        force=True,
        status="done",
        stage="done",
        progress=1.0,
        message=_done_message(qa_summary),
        pages={"total": pages_total, "done": pages_total},
        blocks={"total": translatable, "done": written},
        quality={"mode": mode, "round": quality_round},
        timings=timings_ms,
        result=result,
        qa_summary=qa_summary,
        error=None,
        error_detail=None,
        started_at=None,
        engine_revision=engine_rev,
    )
    _write_history_meta(job_id, job_dir, src, result, qa_summary=qa_summary, quality_mode=mode)


async def _run_quality_loop(
    *,
    job_id: str,
    req: ConvertRequest,
    mode: str,
    max_rounds: int,
    effective_src: Path,
    out_path: Path,
    initial_qa: Any,
    translator: Any,
    job_dir: Path,
    started: float,
    state: Dict[str, Any],
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    """在 worker 线程里跑候选制质量循环（候选翻译是同步引擎调用）。"""
    snapshot = _snapshot_writer(job_id, job_dir, mode)
    try:
        snapshot(0, out_path, initial_qa)
    except Exception:
        pass
    qa_scratch = job_dir / ".qa"
    change_log: List[Dict[str, Any]] = []

    def _update_round(round_no: int, status: str) -> None:
        _safe_update(
            job_id,
            stage="repairing",
            quality={"mode": mode, "round": {"current": int(round_no), "max": int(max_rounds) + 1, "status": status}},
            message={"repair_layout": "第 %d 轮：重排版修复…", "retranslate": "第 %d 轮：重译未翻译文本块…"}.get(
                status, "正在质量修复…"
            )
            % (int(round_no),),
        )

    def run_qa(pdf_path: Path) -> Any:
        _raise_if_cancelled(job_id)
        result = _qa_evaluate(effective_src, pdf_path, mode, qa_scratch)
        stats["qa_ms"] = int(stats.get("qa_ms") or 0) + int((result.timings_ms or {}).get("qa") or 0)
        return result

    def make_candidate(round_no: int, action: Any) -> Path:
        _raise_if_cancelled(job_id)
        candidate = job_dir / CANDIDATE_DIR / f"round-{round_no}.pdf"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        started_at = time.time()
        if action is quality_module.Action.REPAIR_LAYOUT:
            _update_round(round_no, "repair_layout")
            scale = _layout_scale_for_round(round_no)
            _translate_pdf_to(effective_src, candidate, translator, font_scale=scale, skip_overflow=True)
        else:
            _update_round(round_no, "retranslate")
            prompts = _prompts_for_pages(effective_src, [issue.get("page") for issue in state["issues"]])
            if prompts:
                try:
                    translator.invalidate(prompts)
                except Exception:
                    pass
            _translate_pdf_to(effective_src, candidate, translator, font_scale=FONT_SCALE, skip_overflow=False)
        stats["repair_ms"] = int(stats.get("repair_ms") or 0) + int((time.time() - started_at) * 1000)
        change_log.append({"round": round_no, "action": getattr(action, "value", str(action))})
        return candidate

    def on_progress(round_no: int, action: str, total_rounds: int) -> None:
        _safe_update(
            job_id,
            quality={"mode": mode, "round": {"current": int(round_no), "max": int(total_rounds) + 1, "status": action}},
            message=f"质量修复第 {round_no}/{total_rounds} 轮（{action}）…",
        )

    def snapshot_cb(round_no: int, pdf_path: Path, qa_result: Any) -> None:
        state["issues"] = list(getattr(qa_result, "issues", []) or [])
        snapshot(round_no, pdf_path, qa_result)

    outcome = await asyncio.to_thread(
        qa_loop_module.run_quality_loop,
        initial_pdf=out_path,
        initial_qa=initial_qa,
        mode=mode,
        max_rounds=int(max_rounds),
        make_candidate=make_candidate,
        run_qa=run_qa,
        on_progress=on_progress,
        cancel_check=lambda: _raise_if_cancelled(job_id),
        snapshot=snapshot_cb,
        started_at=started,
        time_budget_s=qa_loop_module.quality_time_budget_s(),
        cancel_exceptions=(JobCanceled,),
    )
    return {
        "best_pdf": outcome.best_pdf,
        "best_qa": outcome.best_qa,
        "passes_run": int(outcome.passes_run),
        "pass_history": list(outcome.pass_history),
        "status": outcome.status,
        "stopped_reason": outcome.stopped_reason,
        "repair_attempted": bool(outcome.repair_attempted),
        "timings": {"qa": int(stats.get("qa_ms") or 0), "repair": int(stats.get("repair_ms") or 0)},
        "change_log": change_log,
    }


# =================================================================
# 9. 汇总字段 / 历史记录
# =================================================================

def _timings_payload(translate_ms: int, qa_ms: int, repair_ms: int, started: float) -> Dict[str, Any]:
    return {
        "translate": int(translate_ms),
        "qa": int(qa_ms),
        "repair": int(repair_ms),
        "total": int((time.time() - started) * 1000),
    }


def _cache_stats(translator: Any) -> Dict[str, int]:
    stats = getattr(translator, "cache_stats", None)
    if stats is None:
        return {"hits": 0, "misses": 0, "fallback_blocks": 0}
    try:
        return {str(key): int(value) for key, value in dict(stats.stats()).items()}
    except Exception:
        return {"hits": 0, "misses": 0, "fallback_blocks": 0}


def _terminology_payload() -> Dict[str, Any]:
    return {
        "candidates_added": 0,
        "violations": 0,
        "snapshot_fingerprint": _terms_fingerprint(),
        "conflict_count": 0,
    }


def _config_fingerprint(engine_rev: str, protocol: str, req: ConvertRequest, mode: str) -> str:
    payload = "|".join(
        [engine_rev, protocol, str(req.model), str(req.base_url), mode, str(bool(req.use_cache))]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _done_message(qa: Dict[str, Any]) -> str:
    status = str(qa.get("status") or "")
    if status == "skipped":
        return "转换完成（已跳过译后质量检查）"
    if status == "qa_failed":
        return "转换完成（译后质量检查失败，详见质量报告）"
    errors = int(qa.get("errors") or 0)
    warnings = int(qa.get("warnings") or 0)
    if errors:
        return f"转换完成（质量检查发现 {errors} 个阻断项，详见质量报告）"
    if warnings:
        return f"转换完成（质量检查 {warnings} 条提醒）"
    return "转换完成"


def _job_file_name(job_id: str, src: Path) -> str:
    try:
        job = STORE.get(job_id)
    except Exception:
        job = None
    params = (job or {}).get("params") or {}
    name = str(params.get("file_name") or "")
    return name or src.name


def _write_history_meta(
    job_id: str,
    job_dir: Path,
    src: Path,
    result: Dict[str, Any],
    *,
    qa_summary: Optional[Dict[str, Any]] = None,
    quality_mode: Optional[str] = None,
) -> None:
    """把已完成任务的元信息落到 output/<job_id>/meta.json，供初始页历史列表读取。"""
    meta = {
        "job_id": job_id,
        "name": _job_file_name(job_id, src),
        "created_at": time.time(),
        "original_rel": rel_to_base(src),
        "translated_rel": rel_to_base(Path(result["output_path"])),
        "stats": result,
        "qa": result.get("qa"),
        "qa_summary": qa_summary or result.get("qa_summary"),
        "qa_status": (qa_summary or result.get("qa_summary") or {}).get("status"),
        "quality_mode": quality_mode or result.get("quality_mode"),
        "elapsed_ms": result.get("elapsed_ms"),
        "pages": result.get("pages"),
        "blocks": result.get("blocks"),
        "has_translated": True,
    }
    target = job_dir / HISTORY_META
    temp = job_dir / f"{HISTORY_META}.tmp"
    try:
        temp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _history_item(entry: Path) -> Optional[Dict[str, Any]]:
    """读取单个 output/<job_id>/ 的历史记录；产物缺失时返回 None。

    新任务读 meta.json；早于历史功能生成的旧任务从目录内容兜底恢复。
    """
    if not entry.is_dir() or not JOB_ID_RE.match(entry.name):
        return None
    meta_path = entry / HISTORY_META
    meta: Dict[str, Any] = {}
    if meta_path.is_file():
        try:
            parsed = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                meta = parsed
        except (OSError, ValueError):
            meta = {}

    translated_rel = str(meta.get("translated_rel") or "")
    if not translated_rel:
        legacy = sorted(entry.glob("*.zh.pdf"))
        if not legacy:
            return None
        translated_rel = rel_to_base(legacy[0])
    translated = BASE_DIR / translated_rel
    try:
        ensure_inside(translated, BASE_DIR)
    except HTTPException:
        return None
    if not translated.is_file():
        return None

    original_rel = str(meta.get("original_rel") or "")
    if not original_rel:
        cached = sorted((CACHE_DIR / entry.name).glob("*.pdf")) if (CACHE_DIR / entry.name).is_dir() else []
        if cached:
            original_rel = rel_to_base(cached[0])
    original = BASE_DIR / original_rel if original_rel else None
    if original is not None:
        try:
            ensure_inside(original, BASE_DIR)
        except HTTPException:
            original = None
    if original is None or not original.is_file():
        original_rel = translated_rel   # 源副本丢失时至少保证阅读器左右两侧都能载入

    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    if not stats:
        stats = {
            "pages": _pdf_page_count(translated),
            "size_bytes": translated.stat().st_size,
            "output_path": str(translated),
            "font": FONT_LABEL,
            "engine": f"pdf_zh_translator {ENGINE_VERSION}",
        }

    name = str(meta.get("name") or "")
    if not name:
        name = translated.name[:-7] if translated.name.lower().endswith(".zh.pdf") else translated.name
    qa_summary = meta.get("qa_summary") if isinstance(meta.get("qa_summary"), dict) else None
    if qa_summary is None:
        legacy_qa = meta.get("qa") if isinstance(meta.get("qa"), dict) else stats.get("qa")
        qa_summary = _legacy_qa_summary(legacy_qa, entry.name)
    quality_mode = str(meta.get("quality_mode") or stats.get("quality_mode") or "off")
    elapsed_ms = meta.get("elapsed_ms")
    if not isinstance(elapsed_ms, (int, float)):
        elapsed_ms = stats.get("elapsed_ms") if isinstance(stats.get("elapsed_ms"), (int, float)) else 0
    pages = meta.get("pages")
    if not isinstance(pages, int):
        pages = int(stats.get("pages") or 0)
    blocks = meta.get("blocks")
    if not isinstance(blocks, int):
        blocks = int(stats.get("blocks") or 0)
    return {
        "job_id": entry.name,
        "name": name,
        "created_at": float(meta.get("created_at") or translated.stat().st_mtime),
        "original_url": f"/api/files/{original_rel}",
        "translated_url": f"/api/files/{translated_rel}",
        "stats": stats,
        "qa": meta.get("qa") if isinstance(meta.get("qa"), dict) else None,
        "qa_summary": qa_summary,
        "qa_status": str((qa_summary or {}).get("status") or ""),
        "quality_mode": quality_mode,
        "elapsed_ms": int(elapsed_ms or 0),
        "pages": pages,
        "blocks": blocks,
        "has_translated": True,
    }


def _legacy_qa_summary(legacy_qa: Any, job_id: str) -> Dict[str, Any]:
    value = legacy_qa if isinstance(legacy_qa, dict) else {}
    errors = int(value.get("errors") or 0)
    warnings = int(value.get("warnings") or 0)
    total = int(value.get("total") or 0)
    status = value.get("status")
    if not status:
        if value.get("skipped"):
            status = "skipped"
        elif errors:
            status = "failed"
        elif warnings:
            status = "warning"
        else:
            status = "passed"
    return {
        "errors": errors,
        "warnings": warnings,
        "total": total,
        "status": str(status),
        "report_url": f"/api/jobs/{job_id}/qa",
    }


# =================================================================
# 10. Job detail（GET /api/jobs/{id}）
# =================================================================

def _job_detail(job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(job.get("job_id") or "")
    status = str(job.get("status") or "queued")
    stage = str(job.get("stage") or stage_for_status(status))
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    mode = str(params.get("quality_mode") or _default_quality_mode())
    pages = job.get("pages") if isinstance(job.get("pages"), dict) else {}
    pages_total = int(pages.get("total") or result.get("pages") or 0)
    pages_done = int(pages.get("done") or 0)
    if pages_total > 0:
        pages_done = max(0, min(pages_done, pages_total))
    blocks = job.get("blocks") if isinstance(job.get("blocks"), dict) else {}
    blocks_total = int(blocks.get("total") or result.get("blocks") or 0)
    blocks_done = int(blocks.get("done") or result.get("translated_blocks") or 0)
    if blocks_total > 0:
        blocks_done = max(0, min(blocks_done, blocks_total))
    qa_summary = job.get("qa_summary")
    if not isinstance(qa_summary, dict):
        qa_summary = result.get("qa_summary")
    if not isinstance(qa_summary, dict):
        qa_summary = _legacy_qa_summary(None, job_id)
        qa_summary["status"] = "skipped" if mode == "off" else ("pending" if not is_terminal(status) else "warning")
    quality = job.get("quality") if isinstance(job.get("quality"), dict) else {}
    quality_round = quality.get("round")
    if not isinstance(quality_round, dict):
        quality_round = {
            "current": 0,
            "max": quality_module.max_rounds_for_mode(mode) + (0 if mode == "off" else 1),
            "status": "skipped" if mode == "off" else "pending",
        }
    error_detail = job.get("error_detail") if isinstance(job.get("error_detail"), dict) else None
    files = {
        "original_url": result.get("original_url") or _source_url(job),
        "translated_url": result.get("translated_url") or None,
    }
    return {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "progress": float(job.get("progress") or 0.0),
        "message": str(job.get("message") or ""),
        "pages_done": pages_done,
        "pages_total": pages_total,
        "blocks_done": blocks_done,
        "blocks_total": blocks_total,
        "eta_seconds": _eta_seconds(job, status),
        "queue_position": STORE.queue_position(job_id) if status == "queued" else 0,
        "quality_round": quality_round,
        "quality_mode": mode,
        "can_cancel": job_can_cancel(status),
        "error": job.get("error"),
        "error_code": (error_detail or {}).get("code"),
        "retryable": bool((error_detail or {}).get("retryable", False)),
        "error_detail": error_detail,
        "qa_summary": qa_summary,
        "files": files,
        "timings_ms": _normalize_timings(job.get("timings")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "attempt": int(job.get("attempt") or 0),
        "params": params,
        "result": result or None,
    }


def _normalize_timings(raw: Any) -> Dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    output: Dict[str, Any] = {}
    for key in ("translate", "qa", "repair", "total"):
        item = value.get(key)
        output[key] = int(item) if isinstance(item, (int, float)) else None
    if output["total"] is None:
        elapsed = value.get("elapsed_ms")
        output["total"] = int(elapsed) if isinstance(elapsed, (int, float)) else None
    return output


def _source_url(job: Dict[str, Any]) -> Optional[str]:
    source_path = job.get("source_path")
    if not source_path:
        return None
    try:
        return f"/api/files/{rel_to_base(Path(str(source_path)))}"
    except ValueError:
        return None


def _eta_seconds(job: Dict[str, Any], status: str) -> Optional[int]:
    if status != "running":
        return None
    progress = float(job.get("progress") or 0.0)
    started_at = job.get("started_at")
    if not isinstance(started_at, (int, float)) or progress <= 0.05 or progress >= 1.0:
        return None
    elapsed = max(0.0, time.time() - float(started_at))
    remaining = elapsed * (1.0 - progress) / progress
    return int(max(0, min(remaining, 6 * 3600)))


# =================================================================
# 11. FastAPI 应用
# =================================================================

app = FastAPI(title="paperex API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tauri://localhost / http://tauri.localhost / vite dev
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)


@app.exception_handler(ApiError)
async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    payload = {
        "detail": exc.message,
        "error": exc.as_dict(),
        "code": exc.code,
        "message": exc.message,
        "hint": exc.hint,
        "retryable": exc.retryable,
    }
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.get("/")
def root() -> Dict[str, Any]:
    return {"app": APP_NAME, "version": APP_VERSION, "docs": "/docs"}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    try:
        cache_payload = global_cache.stats()
    except Exception:  # pragma: no cover - 缓存目录异常不应影响健康检查
        cache_payload = {"dir": str(global_cache.cache_dir()), "files": 0, "bytes": 0, "entries": 0}
    try:
        terms_total = int(corpus_stats().get("_total") or 0)
    except Exception:
        terms_total = 0
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "appdata": str(BASE_DIR),
        "cache_dir": str(CACHE_DIR),
        "output_dir": str(OUTPUT_DIR),
        "jobs_dir": str(JOBS_DIR),
        "font": FONT_LABEL,
        "font_path": FONT_PATH,
        "key_configured": bool(_resolve_api_key()),
        "pymupdf": fitz.VersionBind,
        # ---- v1.1 扩展 ----
        "engine": engine_info(),
        "engine_version": ENGINE_VERSION,
        "engine_features": [
            "layout_profiles",
            "structure_classification",
            "protected_regions",
            "formula_placeholders",
            "terminology_injection",
            "structure_aware_prompts",
            "jsonl_translation_cache",
            "placeholder_retry_ladder",
            "quality_retry",
            "cjk_typesetting",
            "post_translation_qa",
            "page_visual_qa",
        ],
        "providers": providers_module.providers_payload(),
        "terminology": terms_total,
        "dry_run": _dry_run(),
        "quality": {
            "default_mode": _default_quality_mode(),
            "modes": list(quality_module.QA_MODES),
            "strict_codes": sorted(quality_module.strict_codes()),
        },
        "features": {
            "isolated_qa": True,
            "quality_loop": True,
            "job_persistence": True,
            "cancel": True,
            "ocr": True,
            "global_cache": True,
            "terminology_api": True,
            "models_api": True,
            "qa_report": True,
            "quality_round": True,
            "models": True,
            "download": True,
            "terminology": True,
            "structured_errors": True,
        },
        "cache": cache_payload,
        "ocr": ocr_module.status(),
    }


@app.get("/api/models")
def list_models() -> Dict[str, Any]:
    payload = providers_module.models_payload()
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        items = providers_module.model_catalog()
    payload["items"] = items
    payload["models"] = items
    return payload


@app.post("/api/test-connection")
async def test_connection(req: TestConnectionRequest) -> Dict[str, Any]:
    api_key = (req.api_key or "").strip() or _env_api_key()
    if not api_key:
        return {
            "ok": False,
            "latency_ms": 0,
            "message": "未配置 API Key：请在设置中填写，或由后端环境变量提供",
            "code": "auth_failed",
            "retryable": False,
        }
    try:
        return await asyncio.to_thread(
            providers_module.test_connection,
            base_url=req.base_url,
            model=req.model,
            api_key=api_key,
            timeout=20.0,
        )
    except Exception as exc:  # 探活失败也返回结构化结果，不抛 500
        detail = _classify_failure(exc)
        return {
            "ok": False,
            "latency_ms": 0,
            "message": detail.get("message") or "连接测试失败",
            "code": detail.get("code") or "provider_unreachable",
            "retryable": bool(detail.get("retryable")),
        }


@app.get("/api/terminology/search")
def search_terminology(q: str = "", limit: int = 50) -> Dict[str, Any]:
    needle = (q or "").strip()
    data = _terminology_data()
    terms = data["terms"]
    if not needle:
        return {"total": 0, "query": needle, "terms": [], "items": []}
    lowered = needle.casefold()
    matched = [
        term
        for term in terms
        if lowered in str(term["en"]).casefold() or needle in str(term["zh"])
    ]
    limit = max(1, min(int(limit or 50), 200))
    return {
        "total": len(matched),
        "query": needle,
        "terms": matched[:limit],
        "items": matched[:limit],
    }


@app.get("/api/terminology")
def list_terminology(limit: int = 2000) -> Dict[str, Any]:
    data = _terminology_data()
    limit = max(1, min(int(limit or 2000), 5000))
    terms = data["terms"][:limit]
    return {
        "total": int(data["total"]),
        "categories": list(data["categories"]),
        "terms": terms,
        "items": terms,
    }


@app.delete("/api/cache")
def clear_cache() -> Dict[str, Any]:
    removed = 0
    try:
        removed = int(global_cache.clear() or 0)
    except Exception as exc:
        raise ApiError(500, f"清理缓存失败：{exc}", code="internal", hint="检查缓存目录权限") from exc
    return {"ok": True, "freed_bytes": removed, "cache": global_cache.stats()}


@app.post("/api/jobs")
async def create_job(req: ConvertRequest) -> Dict[str, Any]:
    api_key = _resolve_api_key(req)
    if not api_key and not _dry_run():
        raise ApiError(
            400,
            "未配置翻译 API Key：请在界面左侧栏「设置」中填写，或设置环境变量 DEEPSEEK_API_KEY",
            code="auth_failed",
            hint="填写 API Key，或设置 DEEPSEEK_API_KEY / PAPEREX_API_KEY 环境变量",
        )
    if not req.base_url.startswith(("http://", "https://")):
        raise ApiError(
            400,
            "base_url 必须以 http:// 或 https:// 开头",
            code="provider_error",
            hint="检查供应商 base_url 配置",
        )
    mode = _quality_mode_for(req)

    src = Path(req.pdf_path)
    if not src.is_file():
        raise ApiError(
            400,
            f"文件不存在：{req.pdf_path}",
            code="invalid_pdf",
            hint="确认文件路径是否正确，或重新选择 PDF",
        )
    if src.suffix.lower() != ".pdf":
        raise ApiError(400, "只支持 .pdf 文件", code="invalid_pdf", hint="请选择 PDF 文件")
    if src.stat().st_size > MAX_UPLOAD_BYTES:
        raise ApiError(400, "文件超过 120MB，请先拆分", code="too_large", hint="拆分 PDF 后分批提交")

    job_id = uuid.uuid4().hex[:16]
    job_dir = OUTPUT_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)
    source_copy = ensure_inside(job_dir / "source.pdf", BASE_DIR)
    try:
        await asyncio.to_thread(_copy_file, src, source_copy)
    except OSError as exc:
        raise ApiError(500, f"保存源文件失败：{exc}", code="internal", hint="检查磁盘空间") from exc

    options = _translator_options(req, mode)
    max_attempts = int(req.max_attempts or max_attempts_default())
    max_attempts = max(1, min(max_attempts, 8))
    engine_rev = engine_fingerprint()
    params = {
        "file_name": src.name,
        "base_url": req.base_url,
        "model": req.model,
        "target_lang": req.target_lang,
        "quality_mode": mode,
        "thinking": bool(options.get("thinking")),
        "reasoning_effort": str(options.get("reasoning_effort") or "high"),
        "use_cache": True if req.use_cache is None else bool(req.use_cache),
        "max_attempts": max_attempts,
        "engine_revision": engine_rev,
        "dry_run": _dry_run(),
    }
    job = STORE.create(job_id, params=params, source_path=source_copy, attempt=0)
    task = asyncio.create_task(run_job(job_id, req, source_copy, api_key))
    _track_task(job_id, task)
    return {
        "job_id": job_id,
        "status": "queued",
        "created_at": job.get("created_at"),
        "source": rel_to_base(source_copy),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = STORE.get(job_id)
    if job is None:
        raise ApiError(404, "任务不存在或已过期", code="not_found", hint="刷新任务列表")
    return _job_detail(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> Any:
    job = STORE.get(job_id)
    if job is None:
        raise ApiError(404, "任务不存在或已过期", code="not_found", hint="刷新任务列表")
    status = str(job.get("status") or "")
    if is_terminal(status):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "status": status,
                "detail": "任务已结束，无法取消",
                "error": {
                    "code": "canceled" if status in ("cancelled",) else "internal",
                    "message": "任务已结束，无法取消",
                    "hint": "任务已经完成或失败，无需取消",
                    "retryable": False,
                },
            },
        )
    updated = STORE.request_cancel(job_id)
    if updated is None:
        return {"ok": False, "status": status}
    if status == "queued":
        task = _JOB_TASKS.get(job_id)
        if task is not None and not task.done():
            task.cancel()   # 还没拿到引擎锁：直接取消协程
    return {"ok": True, "status": str(updated.get("status") or "cancelling"), "message": "已请求取消任务"}


@app.get("/api/jobs/{job_id}/qa")
def get_qa_report(job_id: str) -> Dict[str, Any]:
    job = STORE.get(job_id)
    if job is None:
        raise ApiError(404, "任务不存在或已过期", code="not_found", hint="刷新任务列表")
    report = qa_module.load_report(OUTPUT_DIR / job_id / QA_REPORT)
    if not isinstance(report, dict):
        raise ApiError(
            404,
            "质量报告尚未生成（任务未完成或 quality_mode=off）",
            code="not_found",
            hint="任务完成后再查询质量报告",
        )
    report.setdefault("report_url", f"/api/jobs/{job_id}/qa")
    # 前端 QaPanel 兼容字段：qa.json 文件保持 v1 schema，仅 HTTP 响应顶层补别名
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    codes: Dict[str, int] = {}
    for item in issues:
        if isinstance(item, dict):
            code = str(item.get("code") or "unknown")
            codes[code] = codes.get(code, 0) + 1
    payload = dict(report)
    payload["errors"] = int(report.get("error_count") or 0)
    payload["warnings"] = int(report.get("warning_count") or 0)
    payload["total"] = int(report.get("issue_count") or 0)
    payload["codes"] = codes
    return payload


def _multipart_available() -> bool:
    """python-multipart 是可选依赖：缺失时不应影响桌面端主链路启动。"""
    try:
        import multipart  # noqa: F401

        return True
    except Exception:
        return False


if _multipart_available():

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
        """浏览器开发模式下的兜底入口（桌面端走 Tauri 原生对话框）。"""
        name = sanitize_filename(file.filename or "upload.pdf")
        if not name.lower().endswith(".pdf"):
            raise ApiError(400, "只支持 .pdf 文件", code="invalid_pdf")

        updir = CACHE_DIR / "uploads"
        os.makedirs(updir, exist_ok=True)
        dest = ensure_inside(updir / f"{uuid.uuid4().hex[:8]}_{name}", BASE_DIR)

        size = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = await file.read(1024 * 512)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    fh.close()
                    os.remove(dest)
                    raise ApiError(400, "文件超过 120MB", code="too_large")
                fh.write(chunk)

        return {"pdf_path": str(dest), "size": size}

else:

    @app.post("/api/upload")
    async def upload_unavailable() -> Dict[str, Any]:
        raise HTTPException(
            status_code=501,
            detail="未安装 python-multipart，浏览器上传不可用；桌面端请直接使用文件选择器",
        )


@app.get("/api/files/{rel_path:path}")
def get_file(rel_path: str, download: int = 0) -> FileResponse:
    """沙盒内文件读取：只能是 BASE_DIR 下的相对路径。"""
    if "\x00" in rel_path or rel_path.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="非法路径")
    target = ensure_inside(BASE_DIR / rel_path, BASE_DIR)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    filename = sanitize_filename(target.name)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(target),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _pdf_page_count(path: Path) -> int:
    try:
        document = fitz.open(str(path))
        try:
            return int(document.page_count)
        finally:
            document.close()
    except Exception:
        return 0


@app.get("/api/history")
def list_history() -> Dict[str, Any]:
    """初始页「翻译记录」：按完成时间倒序返回已完成任务。"""
    items = []
    try:
        entries = list(OUTPUT_DIR.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            item = _history_item(entry)
        except OSError:
            item = None
        if item:
            items.append(item)
    items.sort(key=lambda it: it["created_at"], reverse=True)
    return {"items": items, "total": len(items)}


def _purge_job(job_id: str) -> bool:
    """删除任务的状态、源副本与产物目录。"""
    removed = False
    for root in (OUTPUT_DIR, CACHE_DIR, JOBS_DIR):
        target = root / job_id
        if not target.is_dir():
            continue
        ensure_inside(target, BASE_DIR)
        try:
            shutil.rmtree(target)
            removed = True
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"删除文件失败：{exc}")
    try:
        STORE.delete(job_id)
    except Exception:
        pass
    _JOB_TASKS.pop(job_id, None)
    return removed


def _assert_deletable(job_id: str) -> None:
    job = STORE.get(job_id)
    if job is not None and not is_terminal(str(job.get("status") or "")):
        raise HTTPException(status_code=409, detail="任务正在进行中，暂时不能删除")


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str) -> Dict[str, Any]:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="非法的记录 ID")
    _assert_deletable(job_id)
    if not _purge_job(job_id):
        raise HTTPException(status_code=404, detail="记录不存在或已被删除")
    return {"ok": True, "job_id": job_id}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> Dict[str, Any]:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="非法的任务 ID")
    _assert_deletable(job_id)
    if not _purge_job(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


def _copy_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            chunk = fi.read(1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)


# =================================================================
# 12. 启动：恢复扫描 + 横幅
# =================================================================

async def _recover_jobs() -> Dict[str, Any]:
    """重启恢复：中断任务重新排队（有 Key 时）或标记为可重试失败。

    必须在事件循环线程里调用（需要 ``asyncio.create_task`` 重新投递任务）。
    """
    report = await asyncio.to_thread(
        STORE.recover,
        resume=resume_enabled(),
        engine_revision=engine_fingerprint(),
        max_attempts=max_attempts_default(),
    )
    resumed: List[str] = []
    for job_id in list(report.get("requeue") or []):
        job = STORE.get(job_id)
        if job is None:
            continue
        params = job.get("params") if isinstance(job.get("params"), dict) else {}
        source_path = Path(str(job.get("source_path") or ""))
        if not source_path.is_file():
            _mark_interrupted(job_id, "源文件已丢失，无法恢复任务", "重新提交任务")
            continue
        api_key = _env_api_key()
        if not api_key and not _dry_run():
            _mark_interrupted(job_id, "服务重启后未找到 API Key，无法自动恢复", "重新提交任务即可复用全局缓存")
            continue
        try:
            req = ConvertRequest(
                pdf_path=str(source_path),
                base_url=str(params.get("base_url") or DEFAULT_BASE_URL),
                model=str(params.get("model") or DEFAULT_MODEL),
                target_lang=str(params.get("target_lang") or "简体中文"),
                quality_mode=params.get("quality_mode"),
                thinking=params.get("thinking"),
                reasoning_effort=params.get("reasoning_effort"),
                use_cache=params.get("use_cache"),
                max_attempts=params.get("max_attempts"),
            )
        except Exception:
            _mark_interrupted(job_id, "恢复任务参数无效，请重新提交", "重新提交任务")
            continue
        task = asyncio.create_task(run_job(job_id, req, source_path, api_key))
        _track_task(job_id, task)
        resumed.append(job_id)
    return {
        "requeue": list(report.get("requeue") or []),
        "interrupted": list(report.get("interrupted") or []),
        "canceled": list(report.get("canceled") or []),
        "resumed": resumed,
    }


def _mark_interrupted(job_id: str, message: str, hint: str) -> None:
    _safe_update(
        job_id,
        force=True,
        status="error",
        stage="failed",
        progress=1.0,
        message="任务恢复失败",
        error=message,
        error_detail={"code": "interrupted", "message": message, "hint": hint, "retryable": True},
        started_at=None,
    )


def _listen_address() -> Dict[str, Any]:
    """本次进程实际监听地址：优先命令行 --host/--port，其次 PAPEREX_HOST/PORT，最后默认值。"""
    host = os.environ.get("PAPEREX_HOST") or HOST
    port: Any = os.environ.get("PAPEREX_PORT") or PORT
    argv = [str(item) for item in (sys.argv or [])]
    for index, arg in enumerate(argv):
        if arg == "--host" and index + 1 < len(argv):
            host = argv[index + 1]
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg in ("--port", "-p") and index + 1 < len(argv):
            port = argv[index + 1]
        elif arg.startswith("--port="):
            port = arg.split("=", 1)[1]
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = PORT
    return {"host": str(host), "port": int(port)}


def _startup_banner() -> None:
    terms = 0
    try:
        terms = int(corpus_stats().get("_total") or 0)
    except Exception:
        pass
    address = _listen_address()
    print("=" * 66)
    print(f"  {APP_NAME} 后端 v{APP_VERSION}  (pdf_zh_translator {ENGINE_VERSION})")
    print(f"  沙盒目录 : {BASE_DIR}")
    print(f"  中文字体 : {FONT_LABEL} ({FONT_PATH})")
    print(f"  术语库   : {terms} 条")
    print(f"  PyMuPDF  : {fitz.VersionBind}")
    strict_rounds = quality_module.max_rounds_for_mode("strict")
    print(f"  质量循环 : 默认 {_default_quality_mode()} / strict 最多 {strict_rounds} 轮")
    print(f"  任务状态 : {JOBS_DIR}")
    print(f"  监听地址 : http://{address['host']}:{address['port']}  (仅本机)")
    print("=" * 66)


@app.on_event("startup")
async def _on_startup() -> None:
    _startup_banner()
    try:
        result = await _recover_jobs()
        pending = len(result.get("requeue") or [])
        if pending or result.get("interrupted") or result.get("canceled"):
            print(
                f"[paperex] 恢复扫描：重投 {pending} 个，标记中断 "
                f"{len(result.get('interrupted') or [])} 个，取消 {len(result.get('canceled') or [])} 个"
            )
    except Exception as exc:  # pragma: no cover - 恢复失败不应阻止服务启动
        print(f"[paperex] 恢复扫描失败：{exc}")


if __name__ == "__main__":
    import uvicorn

    _address = _listen_address()
    uvicorn.run(app, host=str(_address["host"]), port=int(_address["port"]), log_level="info")
