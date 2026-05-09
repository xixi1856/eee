"""MCP integration: registry registration with mock clients."""

from __future__ import annotations

import json

import pytest

from edu_agent.config import EduSettings, ToolsetsSettings, ToolsetToggle, ToolsSettings
from edu_agent.mcp.client import MCPClient
from edu_agent.mcp.integration import register_mcp_servers, shutdown_mcp_servers
from edu_agent.mcp.loader import MCPBundle
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import ToolsetRegistry


class _FakeMCPClient(MCPClient):
    def __init__(self) -> None:
        self.server_id = "fake"

    async def connect(self) -> None:
        return

    async def close(self) -> None:
        return

    async def list_tools(self) -> list[ToolSpec]:
        async def _handler(_: dict) -> str:
            return json.dumps({"result": "mock-body"}, ensure_ascii=False)

        return [
            ToolSpec(
                name="mcp.fake.hello",
                description="fake hello",
                input_schema={"type": "object", "properties": {}},
                handler=_handler,
                toolset="mcp",
                permissions=[ToolPermission.EXTERNAL],
            )
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        raise AssertionError("handler should not call through to raw client in this test")


@pytest.mark.asyncio
async def test_register_mcp_servers_registers_prefixed_tools(
    monkeypatch: pytest.MonkeyPatch,
    minimal_edu_settings: EduSettings,
) -> None:
    settings = minimal_edu_settings.model_copy(
        update={
            "toolsets": ToolsetsSettings(entries={"mcp": ToolsetToggle(enabled=True)}),
            "tools": ToolsSettings(
                mcp_servers=[{"uri": "stdio", "name": "fake", "command": "noop"}]
            ),
        }
    )

    def _fake_load(_s: EduSettings) -> list[MCPBundle]:
        return [MCPBundle("fake", _FakeMCPClient())]

    monkeypatch.setattr("edu_agent.mcp.integration.load_mcp_bundles", _fake_load)
    reg = ToolsetRegistry()
    try:
        await register_mcp_servers(settings, reg)
        spec = reg.get_spec("mcp.fake.hello")
        assert spec is not None
        assert spec.toolset == "mcp"
    finally:
        await shutdown_mcp_servers()


@pytest.mark.asyncio
async def test_register_mcp_servers_skips_when_mcp_toolset_disabled(
    monkeypatch: pytest.MonkeyPatch,
    minimal_edu_settings: EduSettings,
) -> None:
    settings = minimal_edu_settings.model_copy(
        update={
            "toolsets": ToolsetsSettings(entries={"mcp": ToolsetToggle(enabled=False)}),
            "tools": ToolsSettings(
                mcp_servers=[{"uri": "stdio", "name": "fake", "command": "noop"}]
            ),
        }
    )

    def _fake_load(_s: EduSettings) -> list[MCPBundle]:
        return [MCPBundle("fake", _FakeMCPClient())]

    monkeypatch.setattr("edu_agent.mcp.integration.load_mcp_bundles", _fake_load)
    reg = ToolsetRegistry()
    await register_mcp_servers(settings, reg)
    assert reg.get_spec("mcp.fake.hello") is None
