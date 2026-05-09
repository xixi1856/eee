"""OpenAI Chat Completions ``tools`` adapter — keeps ToolSpec provider-neutral."""

from __future__ import annotations

from typing import Any

from edu_agent.toolsets.models import ToolSpec


def tool_specs_to_openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    """Build OpenAI ``tools`` list from canonical ToolSpec models."""
    out: list[dict[str, Any]] = []
    for spec in specs:
        inner = {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        }
        out.append({"type": "function", "function": inner})
    return out
