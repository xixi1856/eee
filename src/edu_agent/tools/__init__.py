"""Hermes-style tools package entrypoint.

This package is the single import surface for tool schemas + dispatch:
    - TOOL_SCHEMAS: live list consumed by model function-calling
    - execute_tool(): central dispatch, returns ToolResult
    - refresh_tool_schemas(): keep TOOL_SCHEMAS in sync with registry
"""

from __future__ import annotations

import json

from edu_agent.registry import discover_builtin_tools, registry
from edu_agent.types import ToolResult

TOOL_SCHEMAS: list[dict] = []


def refresh_tool_schemas() -> None:
    """Keep list object stable while syncing schemas from registry."""
    TOOL_SCHEMAS[:] = registry.get_tool_definitions()


def execute_tool(name: str, args: dict) -> ToolResult:
    """Dispatch through central registry, returning a ToolResult.

    Parses the JSON string returned by registry.dispatch() and wraps it in a
    ToolResult so callers can access ``.success``, ``.summary``, ``.error``.
    """
    result_str = registry.dispatch(name, args)
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return ToolResult(tool_name=name, success=True, summary=result_str or "")
    if "error" in data:
        return ToolResult(
            tool_name=name,
            success=False,
            summary="",
            error=data["error"],
        )
    return ToolResult(
        tool_name=name,
        success=True,
        summary=data.get("result", ""),
        payload=data.get("payload"),
    )


# Auto-discover and import all tool modules via AST scan.
discover_builtin_tools()

# Initial sync after built-ins are imported.
refresh_tool_schemas()

