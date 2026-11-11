# -*- coding: utf-8 -*-
"""cache 模块测试：bucket 稳定性、统计、LRU 清理、清空。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import cache


@pytest.fixture()
def cache_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "cache" / "translation"
    monkeypatch.setenv("PAPEREX_CACHE_DIR", str(root))
    monkeypatch.delenv("PAPEREX_CACHE_MAX_MB", raising=False)
    return root


def _write_jsonl(path: Path, entries: int, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"key": f"k{i}", "source": f"s{i}", "translation": f"t{i}"}) for i in range(entries)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_bucket_for_deterministic():
    kwargs = dict(
        protocol="deepseek",
        api_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        engine_revision="rev1",
        prompt_version="prompt1",
        terms_fp="terms1",
    )
    first = cache.bucket_for(**kwargs)
    second = cache.bucket_for(**kwargs)
    assert first == second
    assert len(first) == 16
    assert first.isalnum()


def test_bucket_for_changes_with_model_or_revision():
    base = dict(
        protocol="deepseek",
        api_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        engine_revision="rev1",
        prompt_version="prompt1",
        terms_fp="terms1",
    )
    assert cache.bucket_for(**base) != cache.bucket_for(**{**base, "model": "deepseek-v4-pro"})
    assert cache.bucket_for(**base) != cache.bucket_for(**{**base, "engine_revision": "rev2"})
    assert cache.bucket_for(**base) != cache.bucket_for(**{**base, "terms_fp": "terms2"})


def test_bucket_ignores_trailing_slash():
    first = cache.bucket_for(protocol="p", api_url="https://x/v1/", model="m", engine_revision="r")
    second = cache.bucket_for(protocol="p", api_url="https://x/v1", model="m", engine_revision="r")
    assert first == second


def test_cache_file_bucket_sanitized(cache_root: Path):
    path = cache.cache_file("../evil bucket")
    assert path.parent == cache_root
    assert path.name.endswith(".jsonl")
    assert ".." not in path.name


def test_terms_fingerprint_stable(monkeypatch):
    import cache as cache_module

    monkeypatch.setattr(cache_module, "_get_flat_terms_safe", lambda: {"a": "甲", "b": "乙"}, raising=False)
    first = cache.terms_fingerprint({"b": "乙", "a": "甲"})
    second = cache.terms_fingerprint({"a": "甲", "b": "乙"})
    assert first == second
    assert first != cache.terms_fingerprint({"a": "甲", "b": "丙"})
    assert len(first) == 16


def test_stats_empty(cache_root: Path):
    stats = cache.stats()
    assert stats["files"] == 0
    assert stats["bytes"] == 0
    assert stats["entries"] == 0
    assert stats["dir"] == str(cache_root)


def test_stats_counts_entries_and_bytes(cache_root: Path):
    _write_jsonl(cache_root / "aaa.jsonl", 3)
    _write_jsonl(cache_root / "bbb.jsonl", 2)
    stats = cache.stats()
    assert stats["files"] == 2
    assert stats["entries"] == 5
    assert stats["bytes"] > 0
    # 第二次走索引缓存，结果一致
    again = cache.stats()
    assert again["entries"] == 5
    assert again["files"] == 2


def test_stats_ignores_index_and_tmp(cache_root: Path):
    _write_jsonl(cache_root / "aaa.jsonl", 1)
    (cache_root / "_index.json").write_text("{}", encoding="utf-8")
    (cache_root / "aaa.jsonl.tmp").write_text("x", encoding="utf-8")
    stats = cache.stats()
    assert stats["files"] == 1


def test_prune_removes_oldest(cache_root: Path, monkeypatch):
    monkeypatch.setenv("PAPEREX_CACHE_MAX_MB", "0")
    now = time.time()
    old = _write_jsonl(cache_root / "old.jsonl", 10, mtime=now - 300)
    new = _write_jsonl(cache_root / "new.jsonl", 10, mtime=now)
    freed = cache.prune(max_bytes=0)
    assert freed > 0
    assert not old.exists()
    assert not new.exists()


def test_prune_keeps_under_limit(cache_root: Path):
    _write_jsonl(cache_root / "a.jsonl", 2)
    _write_jsonl(cache_root / "b.jsonl", 2)
    total = cache.stats()["bytes"]
    assert cache.prune(max_bytes=total) == 0
    assert cache.stats()["files"] == 2


def test_clear_returns_freed_bytes(cache_root: Path):
    _write_jsonl(cache_root / "a.jsonl", 2)
    _write_jsonl(cache_root / "b.jsonl", 3)
    size = cache.stats()["bytes"]
    assert cache.clear() == size
    stats = cache.stats()
    assert stats["files"] == 0 and stats["bytes"] == 0 and stats["entries"] == 0


def test_cache_dir_env_override(tmp_path: Path, monkeypatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("PAPEREX_CACHE_DIR", str(target))
    assert cache.cache_dir() == target


def test_cache_dir_appdata_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PAPEREX_CACHE_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cache.cache_dir() == tmp_path / "paperex" / "cache" / "translation"


def test_max_cache_bytes_env(monkeypatch):
    monkeypatch.delenv("PAPEREX_CACHE_MAX_MB", raising=False)
    assert cache.max_cache_bytes() == 500 * 1024 * 1024
    monkeypatch.setenv("PAPEREX_CACHE_MAX_MB", "12")
    assert cache.max_cache_bytes() == 12 * 1024 * 1024
    monkeypatch.setenv("PAPEREX_CACHE_MAX_MB", "bad")
    assert cache.max_cache_bytes() == 500 * 1024 * 1024


def test_bucket_info(cache_root: Path):
    bucket = "0123456789abcdef"
    info = cache.bucket_info(bucket)
    assert info["exists"] is False
    _write_jsonl(cache.cache_file(bucket), 1)
    info = cache.bucket_info(bucket)
    assert info["exists"] is True
    assert info["bytes"] > 0
