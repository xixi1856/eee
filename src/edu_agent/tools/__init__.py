"""Built-in tools package — discovery and test helpers (A4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from edu_agent.config import (
    AgentDefaults,
    EduSettings,
    ProviderCredentials,
    ProvidersSettings,
    RuntimeSettings,
    ToolsSettings,
)
from edu_agent.llm_tools import tool_specs_to_openai_tools
from edu_agent.paths import build_paths
from edu_agent.providers.runtime import resolve_provider_runtime
from edu_agent.runtime_context import TurnRuntimeContext
from edu_agent.toolsets import (
    PermissionChecker,
    ToolRuntime,
    discover_builtin_tools,
    permissive_permission_policy,
    toolset_registry,
)
from edu_agent.types import ToolResult

TOOL_SCHEMAS: list[dict] = []

_FALLBACK_TOOL_SETTINGS: EduSettings | None = None


def _fallback_settings_for_tool_tests() -> EduSettings:
    """Isolated EduSettings with placeholder LLM key — used when ``settings`` is omitted (tests/REPL)."""
    global _FALLBACK_TOOL_SETTINGS
    if _FALLBACK_TOOL_SETTINGS is not None:
        return _FALLBACK_TOOL_SETTINGS
    root = Path.cwd() / ".edu_agent_tool_execute_ws"
    root.mkdir(exist_ok=True)
    skills = root / "skills"
    skills.mkdir(exist_ok=True)
    _FALLBACK_TOOL_SETTINGS = EduSettings(
        agent=AgentDefaults(
            workspace=root,
            model="test-model",
            provider="dashscope",
            skills_dir="skills",
        ),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="sk-test-placeholder",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
        tools=ToolsSettings(),
        runtime=RuntimeSettings(),
    )
    return _FALLBACK_TOOL_SETTINGS


def refresh_tool_schemas(settings: EduSettings | None = None) -> None:
    """Rebuild ``TOOL_SCHEMAS`` from the canonical registry (tests may call)."""
    st = settings or _fallback_settings_for_tool_tests()
    TOOL_SCHEMAS[:] = tool_specs_to_openai_tools(toolset_registry.list_specs(st))


async def execute_tool_async(
    name: str,
    args: dict,
    *,
    settings: EduSettings | None = None,
) -> ToolResult:
    """Awaitable tool execution for tests (full ToolRuntime path)."""
    st = settings or _fallback_settings_for_tool_tests()
    rt = ToolRuntime(
        toolset_registry,
        st,
        PermissionChecker(
            permissive_permission_policy(),
            approve_all=True,
            interactive=False,
        ),
    )
    paths = build_paths(st)
    pr = resolve_provider_runtime(st, None, "main")
    ctx = TurnRuntimeContext(
        settings=st,
        paths=paths,
        provider_runtime=pr,
        user_id="test",
        session_id="test",
        tool_runtime=rt,
    )
    _content, tr = await rt.execute(name, args, ctx)
    return tr


def execute_tool(name: str, args: dict, settings: EduSettings | None = None) -> ToolResult:
    """Sync wrapper for tests without a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute_tool_async(name, args, settings=settings))
    raise RuntimeError("execute_tool() cannot be used inside async; await execute_tool_async()")


discover_builtin_tools()
refresh_tool_schemas()
