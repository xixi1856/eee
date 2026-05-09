"""Token estimation: rough pre-check + tiktoken where available."""

from __future__ import annotations

import json
from typing import Any

from edu_agent.context.models import ContextConfig

_CHARS_PER_TOKEN_ROUGH = 4


def estimate_messages_tokens_rough(messages: list[dict[str, Any]]) -> int:
    """Fast character-based estimate (Hermes-style rough / gateway signal)."""
    total = 0
    for m in messages:
        total += _message_chars_estimate(m) // _CHARS_PER_TOKEN_ROUGH + 4
    return max(total, 0)


def _message_chars_estimate(m: dict[str, Any]) -> int:
    c = 0
    content = m.get("content")
    if isinstance(content, str):
        c += len(content)
    elif isinstance(content, list):
        c += len(json.dumps(content, ensure_ascii=False))
    elif content is not None:
        c += len(json.dumps(content, ensure_ascii=False))
    for tc in m.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            c += len(str(fn.get("name", "")))
            c += len(str(fn.get("arguments", "")))
    if m.get("tool_call_id"):
        c += len(str(m["tool_call_id"]))
    return c


def _encoding_for_model(model_name: str):
    try:
        import tiktoken

        try:
            return tiktoken.encoding_for_model(model_name)
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        return None


def estimate_tokens(message: dict[str, Any], model_name: str) -> int:
    """Best-effort tokens for one OpenAI-shaped message."""
    enc = _encoding_for_model(model_name)
    if enc is None:
        return max(_message_chars_estimate(message) // _CHARS_PER_TOKEN_ROUGH + 4, 1)
    parts: list[str] = []
    role = message.get("role", "")
    parts.append(role)
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))
    for tc in message.get("tool_calls") or []:
        if isinstance(tc, dict):
            parts.append(json.dumps(tc, ensure_ascii=False))
    if message.get("tool_call_id"):
        parts.append(str(message["tool_call_id"]))
    return len(enc.encode("\n".join(parts))) + 4


def estimate_messages_tokens(messages: list[dict[str, Any]], model_name: str) -> int:
    return sum(estimate_tokens(m, model_name) for m in messages)


def get_context_limit(model_name: str, config: ContextConfig) -> int:
    """Effective token budget for conversation (excluding system prompt injection by caller)."""
    _ = model_name
    return max(int(config.model_max_tokens * config.token_limit_percent), 256)


def pre_check_limit(config: ContextConfig) -> int:
    return max(int(config.model_max_tokens * config.pre_check_ratio), 256)
