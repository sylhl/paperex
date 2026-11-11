# -*- coding: utf-8 -*-
"""供应商 / 模型静态目录与连接探活（plan §7）。

目录是纯静态数据，不包含任何 API Key；前端在 ``GET /api/models`` 失败时
可以用同结构的本地常量兜底。协议选择仍以 ``engine_adapter.resolve_vendor_mode``
为准，这里只负责「展示 + 探活」。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 模型条目同时满足 plan §2 的 health 形状（id/label/context/thinking）与
# frontend-spec 4.3.5 的形状（vendor/protocol/efforts/description/default）。
_MODEL_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "deepseek-flash",
        "label": "DeepSeek Flash",
        "vendor": "deepseek",
        "protocol": "native",
        "thinking": True,
        "efforts": ["high", "max"],
        "context": 131072,
        "description": "更快、更省，适合初翻",
        "default": True,
    },
    {
        "id": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "vendor": "deepseek",
        "protocol": "native",
        "thinking": True,
        "efforts": ["high", "max"],
        "context": 262144,
        "description": "质量优先，适合正式稿",
        "default": False,
    },
    {
        "id": "deepseek-chat",
        "label": "DeepSeek Chat（旧版）",
        "vendor": "deepseek",
        "protocol": "openai-compatible",
        "thinking": False,
        "efforts": [],
        "context": 65536,
        "description": "旧版模型，保持向后兼容",
        "default": False,
    },
]

PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "deepseek",
        "label": "DeepSeek 官方",
        "base_url": DEEPSEEK_BASE_URL,
        "protocol": "deepseek",
        "custom": False,
        "models": _MODEL_CATALOG,
    },
    {
        "id": "openai",
        "label": "OpenAI 兼容",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai-compatible",
        "custom": True,
        "models": [
            {
                "id": "gpt-4o-mini",
                "label": "GPT-4o mini",
                "vendor": "openai",
                "protocol": "openai-compatible",
                "thinking": False,
                "efforts": [],
                "context": 128000,
                "description": "OpenAI 兼容端点示例",
                "default": False,
            }
        ],
    },
    {
        "id": "anthropic",
        "label": "Anthropic（可选）",
        "base_url": "https://api.anthropic.com",
        "protocol": "anthropic",
        "custom": True,
        "models": [
            {
                "id": "claude-sonnet-4-5",
                "label": "Claude Sonnet 4.5",
                "vendor": "anthropic",
                "protocol": "anthropic",
                "thinking": False,
                "efforts": [],
                "context": 200000,
                "description": "Anthropic 原生协议（可选）",
                "default": False,
            }
        ],
    },
]

RECOMMENDED: List[Dict[str, str]] = [
    {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "label": "DeepSeek Flash",
        "note": "默认推荐：速度快、成本低，配合 standard 质量模式即可零 error",
    },
    {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "note": "质量优先：适合正式稿或 strict 模式",
    },
]


def providers_payload() -> List[Dict[str, Any]]:
    """health 的 ``providers`` 字段（不含 key）。"""
    return [
        {
            "id": provider["id"],
            "label": provider["label"],
            "base_url": provider["base_url"],
            "models": [
                {
                    "id": model["id"],
                    "label": model["label"],
                    "context": model["context"],
                    "thinking": model["thinking"],
                }
                for model in provider["models"]
            ],
        }
        for provider in PROVIDERS
    ]


def model_catalog() -> List[Dict[str, Any]]:
    """扁平模型列表（frontend-spec 4.3.5 的 ``items`` 形状）。"""
    return [dict(model) for model in _MODEL_CATALOG]


def models_payload() -> Dict[str, Any]:
    """``GET /api/models``：同时提供 plan 的 providers/recommended 与前端兜底 items。"""
    return {
        "providers": providers_payload(),
        "recommended": [dict(item) for item in RECOMMENDED],
        "items": model_catalog(),
    }


def find_model(model_id: str) -> Optional[Dict[str, Any]]:
    key = (model_id or "").strip().lower()
    for model in _MODEL_CATALOG:
        if model["id"].lower() == key:
            return dict(model)
    return None


def provider_for(base_url: str) -> Dict[str, Any]:
    """按 host 猜测供应商；未知返回 custom 占位。"""
    host = (urlparse(base_url or "").hostname or "").lower()
    for provider in PROVIDERS:
        if (urlparse(provider["base_url"]).hostname or "").lower() == host:
            return dict(provider)
    return {
        "id": "custom",
        "label": "自定义",
        "base_url": base_url,
        "protocol": "openai-compatible",
        "custom": True,
        "models": [],
    }


def _auth_headers(base_url: str, api_key: str) -> Dict[str, str]:
    if "anthropic" in (urlparse(base_url).hostname or "").lower():
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {api_key}"}


def _parse_model_ids(payload: Any) -> List[str]:
    if isinstance(payload, dict):
        raw = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("models")
        if isinstance(raw, list):
            ids: List[str] = []
            for item in raw:
                if isinstance(item, dict):
                    model_id = item.get("id") or item.get("name")
                else:
                    model_id = item
                if isinstance(model_id, str) and model_id.strip():
                    ids.append(model_id.strip())
            return ids[:50]
    return []


def _failure(message: str, code: str, *, latency_ms: int = 0, retryable: bool = False):
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "message": message,
        "code": code,
        "retryable": retryable,
    }


def test_connection(
    *,
    base_url: str,
    model: str,
    api_key: Optional[str] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """探活供应商：优先 ``GET {base_url}/models``，失败再发 1-token chat。

    返回 ``{ok,latency_ms,message,models?,code?,retryable?}``；绝不回显 key。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return _failure("base_url 必须以 http:// 或 https:// 开头", "invalid_base_url")
    if not (api_key or "").strip():
        return _failure(
            "缺少 API Key：请在设置中填写，或由后端环境变量提供",
            "auth_failed",
        )
    headers = _auth_headers(base, api_key.strip())
    started = time.perf_counter()

    def _latency() -> int:
        return max(0, int(round((time.perf_counter() - started) * 1000)))

    try:
        with httpx.Client(timeout=timeout) as client:
            try:
                response = client.get(f"{base}/models", headers=headers)
            except httpx.HTTPError:
                response = None
            if response is not None:
                if response.status_code == 200:
                    ids = _parse_model_ids(_safe_json(response))
                    return {
                        "ok": True,
                        "latency_ms": _latency(),
                        "message": (
                            f"连接成功（{len(ids)} 个可用模型）"
                            if ids
                            else "连接成功"
                        ),
                        "models": ids,
                    }
                if response.status_code in (401, 403):
                    return _failure(
                        "鉴权失败（401/403）：API Key 无效或没有该模型权限",
                        "auth_failed",
                        latency_ms=_latency(),
                    )
            # /models 不可用（404/405/401 以外的 5xx 等）：退回最小 chat 请求
            payload, url = _probe_request(base, model, headers)
            try:
                response = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                message = f"无法连接供应商：{_describe_httpx_error(exc)}"
                return _failure(message, "provider_unreachable", latency_ms=_latency(), retryable=True)
            return _probe_result(response, _latency())
    except httpx.HTTPError as exc:  # pragma: no cover - 连接层兜底
        return _failure(
            f"无法连接供应商：{_describe_httpx_error(exc)}",
            "provider_unreachable",
            latency_ms=_latency(),
            retryable=True,
        )


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _describe_httpx_error(exc: httpx.HTTPError) -> str:
    text = str(exc) or exc.__class__.__name__
    return text[:200]


def _probe_request(base_url: str, model: str, headers: Dict[str, str]):
    host = (urlparse(base_url).hostname or "").lower()
    if "anthropic" in host:
        url = f"{base_url}/v1/messages" if not base_url.endswith("/v1") else f"{base_url}/messages"
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        return payload, url
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    return payload, url


def _probe_result(response: httpx.Response, latency_ms: int) -> Dict[str, Any]:
    status = response.status_code
    if status < 400:
        if "anthropic" in str(response.url):
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            ids = [payload.get("model")] if isinstance(payload, dict) and payload.get("model") else []
        else:
            payload = _safe_json(response)
            ids = []
            if isinstance(payload, dict) and payload.get("model"):
                ids = [str(payload["model"])]
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "message": f"连接成功（{latency_ms} ms）",
            "models": [item for item in ids if item],
        }
    if status in (401, 403):
        return _failure(
            "鉴权失败（401/403）：API Key 无效或没有该模型权限",
            "auth_failed",
            latency_ms=latency_ms,
        )
    detail = ""
    payload = _safe_json(response)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("type") or "")
        elif error:
            detail = str(error)
        detail = detail or str(payload.get("message") or "")
    message = f"供应商返回 HTTP {status}" + (f"：{detail[:300]}" if detail else "")
    code = "provider_unreachable" if status >= 500 else "provider_error"
    return _failure(message, code, latency_ms=latency_ms, retryable=True)


__all__ = [
    "DEEPSEEK_BASE_URL",
    "PROVIDERS",
    "RECOMMENDED",
    "find_model",
    "model_catalog",
    "models_payload",
    "provider_for",
    "providers_payload",
    "test_connection",
]
