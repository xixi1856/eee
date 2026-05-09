"""JSON helpers for tool handlers returning legacy string payloads."""

from __future__ import annotations

import json
from typing import Any


def tool_result(data: Any, **extra: Any) -> str:
    payload: dict[str, Any] = {"result": data}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def tool_error(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)
