"""ToolRuntime: schema validation, permissions, formatting."""

from __future__ import annotations

import asyncio

import pytest

from edu_agent.config import EduSettings, ToolPermissionPolicy, ToolsSettings
from edu_agent.paths import build_paths
from edu_agent.providers.runtime import resolve_provider_runtime
from edu_agent.runtime_context import TurnRuntimeContext
from edu_agent.toolsets import ToolRuntime
from edu_agent.toolsets.permissions import PermissionChecker, permissive_permission_policy
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import ToolsetRegistry


async def _echo(args: dict) -> dict:
    return {"result": f"echo:{args.get('q', '')}"}


@pytest.fixture()
def isolated_registry() -> ToolsetRegistry:
    r = ToolsetRegistry()
    r.register(
        ToolSpec(
            name="echo_tool",
            description="Echo",
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            handler=_echo,
            toolset="default",
            permissions=[ToolPermission.READ],
        ),
        overwrite=True,
    )
    return r


@pytest.fixture()
def runtime_ctx(minimal_edu_settings: EduSettings, isolated_registry: ToolsetRegistry):
    paths = build_paths(minimal_edu_settings)
    pr = resolve_provider_runtime(minimal_edu_settings, None, "main")
    rt = ToolRuntime(
        isolated_registry,
        minimal_edu_settings,
        PermissionChecker(
            permissive_permission_policy(),
            approve_all=True,
            interactive=False,
        ),
    )
    return TurnRuntimeContext(
        settings=minimal_edu_settings,
        paths=paths,
        provider_runtime=pr,
        user_id="u",
        session_id="s",
        tool_runtime=rt,
    )


@pytest.mark.asyncio
async def test_execute_rejects_invalid_args(
    runtime_ctx: TurnRuntimeContext,
) -> None:
    trt = runtime_ctx.tool_runtime
    assert trt is not None
    content, tr = await trt.execute("echo_tool", {}, runtime_ctx)
    assert tr.success is False
    assert "参数校验" in content or "校验" in content


@pytest.mark.asyncio
async def test_execute_runs_handler(
    runtime_ctx: TurnRuntimeContext,
) -> None:
    trt = runtime_ctx.tool_runtime
    assert trt is not None
    content, tr = await trt.execute("echo_tool", {"q": "hi"}, runtime_ctx)
    assert tr.success is True
    assert "echo:hi" in content


@pytest.mark.asyncio
async def test_execute_timeout(minimal_edu_settings: EduSettings) -> None:
    async def slow(_: dict) -> str:
        await asyncio.sleep(10.0)
        return "nope"

    reg = ToolsetRegistry()
    reg.register(
        ToolSpec(
            name="slow_tool",
            description="Slow",
            input_schema={"type": "object", "properties": {}},
            handler=slow,
            toolset="default",
            permissions=[ToolPermission.READ],
            timeout_sec=0.05,
        ),
        overwrite=True,
    )
    trt = ToolRuntime(
        reg,
        minimal_edu_settings,
        PermissionChecker(
            permissive_permission_policy(),
            approve_all=True,
            interactive=False,
        ),
    )
    paths = build_paths(minimal_edu_settings)
    pr = resolve_provider_runtime(minimal_edu_settings, None, "main")
    ctx = TurnRuntimeContext(
        settings=minimal_edu_settings,
        paths=paths,
        provider_runtime=pr,
        user_id="u",
        session_id="s",
        tool_runtime=trt,
    )
    _content, tr = await trt.execute("slow_tool", {}, ctx)
    assert tr.success is False


def test_discover_and_register_returns_empty() -> None:
    from edu_agent.skill_tool_registry import discover_and_register

    assert discover_and_register([]) == []


@pytest.mark.asyncio
async def test_network_tool_blocked_when_policy_denies(minimal_edu_settings: EduSettings) -> None:
    strict = minimal_edu_settings.model_copy(
        update={"tools": ToolsSettings(permission_policy=ToolPermissionPolicy())},
    )
    reg = ToolsetRegistry()

    async def _net(_: dict) -> str:
        return "should-not-run"

    reg.register(
        ToolSpec(
            name="net_probe",
            description="network probe",
            input_schema={"type": "object", "properties": {}},
            handler=_net,
            toolset="default",
            permissions=[ToolPermission.NETWORK],
        ),
        overwrite=True,
    )
    trt = ToolRuntime(
        reg,
        strict,
        PermissionChecker(
            strict.tools.permission_policy,
            approve_all=True,
            interactive=False,
        ),
    )
    paths = build_paths(strict)
    pr = resolve_provider_runtime(strict, None, "main")
    ctx = TurnRuntimeContext(
        settings=strict,
        paths=paths,
        provider_runtime=pr,
        user_id="u",
        session_id="s",
        tool_runtime=trt,
    )
    _content, tr = await trt.execute("net_probe", {}, ctx)
    assert tr.success is False
    assert ("权限" in (tr.error or "")) or ("权限" in _content)


@pytest.mark.asyncio
async def test_retry_on_connection_error(minimal_edu_settings: EduSettings) -> None:
    reg = ToolsetRegistry()
    calls = {"n": 0}

    async def _flaky(_: dict) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("temporary")
        return "ok"

    reg.register(
        ToolSpec(
            name="flaky_tool",
            description="Flaky",
            input_schema={"type": "object", "properties": {}},
            handler=_flaky,
            toolset="default",
            permissions=[ToolPermission.READ],
        ),
        overwrite=True,
    )
    trt = ToolRuntime(
        reg,
        minimal_edu_settings,
        PermissionChecker(
            permissive_permission_policy(),
            approve_all=True,
            interactive=False,
        ),
    )
    paths = build_paths(minimal_edu_settings)
    pr = resolve_provider_runtime(minimal_edu_settings, None, "main")
    ctx = TurnRuntimeContext(
        settings=minimal_edu_settings,
        paths=paths,
        provider_runtime=pr,
        user_id="u",
        session_id="s",
        tool_runtime=trt,
    )
    _content, tr = await trt.execute("flaky_tool", {}, ctx)
    assert tr.success is True
    assert calls["n"] == 3


def test_list_specs_excludes_disabled_names(minimal_edu_settings: EduSettings) -> None:
    reg = ToolsetRegistry()
    reg.register(
        ToolSpec(
            name="z_tool",
            description="z",
            input_schema={"type": "object", "properties": {}},
            handler=_echo,
            toolset="default",
            permissions=[ToolPermission.READ],
        ),
        overwrite=True,
    )
    specs = reg.list_specs(minimal_edu_settings, disabled_names=frozenset({"z_tool"}))
    assert all(s.name != "z_tool" for s in specs)


@pytest.mark.asyncio
async def test_execute_disabled_name(minimal_edu_settings: EduSettings) -> None:
    reg = ToolsetRegistry()
    reg.register(
        ToolSpec(
            name="blocked_tool",
            description="b",
            input_schema={"type": "object", "properties": {}},
            handler=_echo,
            toolset="default",
            permissions=[ToolPermission.READ],
        ),
        overwrite=True,
    )
    trt = ToolRuntime(
        reg,
        minimal_edu_settings,
        PermissionChecker(
            permissive_permission_policy(),
            approve_all=True,
            interactive=False,
        ),
    )
    paths = build_paths(minimal_edu_settings)
    pr = resolve_provider_runtime(minimal_edu_settings, None, "main")
    ctx = TurnRuntimeContext(
        settings=minimal_edu_settings,
        paths=paths,
        provider_runtime=pr,
        user_id="u",
        session_id="s",
        tool_runtime=trt,
    )
    _content, tr = await trt.execute(
        "blocked_tool",
        {"q": "x"},
        ctx,
        disabled_names=frozenset({"blocked_tool"}),
    )
    assert tr.success is False
    assert "禁用" in _content or "禁用" in (tr.error or "")
