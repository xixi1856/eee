"""Format and truncate tool outputs for the model (A4)."""

from __future__ import annotations

import json
from typing import Any


def format_tool_result_for_model(
    value: Any,
    *,
    max_chars: int = 24_000,
) -> str:
    """Serialize handler output to a string safe for role=tool content."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 40
    return text[:head] + "\n…[truncated by ToolRuntime]…\n" + text[-tail:]
