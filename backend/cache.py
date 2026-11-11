# -*- coding: utf-8 -*-
"""跨任务内容寻址翻译缓存（plan §5）。

每个 bucket 一个 JSONL：``%APPDATA%/paperex/cache/translation/<bucket>.jsonl``。
bucket = sha256(protocol|api_url|model|source_lang|target_lang|engine_revision|
prompt_version|terms_fingerprint)[:16]。翻译读写全局缓存，job 目录下的
``translation-cache.jsonl`` 仅作可导出副本；任务结束/删除历史都不清理全局缓存。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_MB = 500
INDEX_NAME = "_index.json"
_INDEX_LOCK = threading.RLock()


def _appdata_root() -> Path:
    override = (os.environ.get("PAPEREX_APPDATA") or "").strip()
    if override:
        return Path(override).expanduser()
    appdata = (os.environ.get("APPDATA") or "").strip()
    if appdata:
        return Path(appdata)
    return Path.home() / "AppData" / "Roaming"


def cache_dir() -> Path:
    override = (os.environ.get("PAPEREX_CACHE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return _appdata_root() / "paperex" / "cache" / "translation"


def ensure_cache_dir() -> Path:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def max_cache_bytes() -> int:
    raw = (os.environ.get("PAPEREX_CACHE_MAX_MB") or "").strip()
    try:
        megabytes = int(float(raw)) if raw else DEFAULT_MAX_MB
    except ValueError:
        megabytes = DEFAULT_MAX_MB
    return max(0, megabytes) * 1024 * 1024


def _sha_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def terms_fingerprint(terms: Optional[Dict[str, str]] = None) -> str:
    """术语表指纹：排序后的 en→zh 键值对 sha256 前 16 位。"""
    if terms is None:
        try:
            from pdf_zh_translator import corpus  # type: ignore

            terms = dict(corpus._get_flat_terms())  # noqa: SLF001 - 引擎公开的扁平术语
        except Exception:  # pragma: no cover - 引擎缺省时按空表
            terms = {}
    lines = sorted(f"{en}\x1f{zh}" for en, zh in (terms or {}).items())
    return _sha_hex("\n".join(lines))[:16]


def bucket_for(
    *,
    protocol: str,
    api_url: str,
    model: str,
    source_lang: str = "en",
    target_lang: str = "zh",
    engine_revision: str,
    prompt_version: str = "",
    terms_fp: str = "",
) -> str:
    payload = "|".join(
        [
            (protocol or "").strip(),
            (api_url or "").strip().rstrip("/"),
            (model or "").strip(),
            (source_lang or "").strip(),
            (target_lang or "").strip(),
            (engine_revision or "").strip(),
            (prompt_version or "").strip(),
            (terms_fp or "").strip(),
        ]
    )
    return _sha_hex(payload)[:16]


def cache_file(bucket: str) -> Path:
    safe = "".join(ch for ch in bucket if ch.isalnum() or ch in "-_")[:64] or "empty"
    return cache_dir() / f"{safe}.jsonl"


def _iter_cache_files() -> List[Path]:
    directory = cache_dir()
    if not directory.is_dir():
        return []
    return [
        path
        for path in directory.glob("*.jsonl")
        if path.is_file() and path.name != INDEX_NAME
    ]


def _index_path() -> Path:
    return cache_dir() / INDEX_NAME


def _read_index() -> Dict[str, Any]:
    path = _index_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        return {"files": {}}
    return payload


def _write_index(index: Dict[str, Any]) -> None:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    index["updated_at"] = time.time()
    try:
        tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:  # pragma: no cover - 只读目录时静默
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _count_lines(path: Path) -> int:
    count = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    except OSError:
        return 0
    return count


def stats() -> Dict[str, Any]:
    """``{dir,files,bytes,entries,max_bytes}``；entries 用 mtime+size 指纹缓存避免重复数行。"""
    directory = cache_dir()
    files = _iter_cache_files()
    total_bytes = 0
    entries = 0
    with _INDEX_LOCK:
        index = _read_index()
        known = dict(index.get("files") or {})
        changed = False
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            total_bytes += int(stat.st_size)
            signature = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
            cached = known.get(path.name)
            if (
                isinstance(cached, dict)
                and int(cached.get("entries") or 0) > 0
                and cached.get("size") == signature["size"]
                and cached.get("mtime_ns") == signature["mtime_ns"]
            ):
                entries += int(cached["entries"])
            else:
                count = _count_lines(path)
                known[path.name] = {**signature, "entries": count}
                changed = True
                entries += count
        alive = {path.name for path in files}
        pruned = {name: value for name, value in known.items() if name in alive}
        if pruned != known:
            changed = True
        if changed:
            index["files"] = pruned
            _write_index(index)
    return {
        "dir": str(directory),
        "files": len(files),
        "bytes": total_bytes,
        "entries": entries,
        "max_bytes": max_cache_bytes(),
    }


def prune(*, max_bytes: Optional[int] = None) -> int:
    """按 mtime 从旧到新删除，直到总大小不超过上限；返回释放字节数。"""
    limit = max_cache_bytes() if max_bytes is None else max(0, int(max_bytes))
    files: List[Tuple[float, int, Path]] = []
    for path in _iter_cache_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((float(stat.st_mtime), int(stat.st_size), path))
    total = sum(size for _, size, _ in files)
    if total <= limit:
        return 0
    freed = 0
    for _, size, path in sorted(files, key=lambda item: item[0]):
        if total - freed <= limit:
            break
        try:
            path.unlink()
            freed += size
        except OSError:
            continue
    if freed:
        with _INDEX_LOCK:
            index = _read_index()
            alive = {path.name for path in _iter_cache_files()}
            index["files"] = {
                name: value for name, value in (index.get("files") or {}).items() if name in alive
            }
            _write_index(index)
    return freed


def clear() -> int:
    """清空全局缓存，返回释放字节数。"""
    freed = 0
    for path in _iter_cache_files():
        try:
            freed += int(path.stat().st_size)
            path.unlink()
        except OSError:
            continue
    with _INDEX_LOCK:
        try:
            _index_path().unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
    return freed


def bucket_info(bucket: str) -> Dict[str, Any]:
    path = cache_file(bucket)
    try:
        size = int(path.stat().st_size)
    except OSError:
        size = 0
    return {"bucket": bucket, "path": str(path), "exists": size > 0, "bytes": size}


__all__ = [
    "DEFAULT_MAX_MB",
    "bucket_for",
    "bucket_info",
    "cache_dir",
    "cache_file",
    "clear",
    "ensure_cache_dir",
    "max_cache_bytes",
    "prune",
    "stats",
    "terms_fingerprint",
]
