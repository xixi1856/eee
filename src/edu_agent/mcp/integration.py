"""Wire MCP clients into ``ToolsetRegistry`` (dynamic tools)."""

from __future__ import annotations

import logging

from edu_agent.config import EduSettings, ToolsetsSettings
from edu_agent.mcp.loader import load_mcp_bundles
from edu_agent.toolsets.registry import ToolsetRegistry

logger = logging.getLogger(__name__)

_ACTIVE: list = []


async def register_mcp_servers(settings: EduSettings, registry: ToolsetRegistry) -> None:
    """Connect configured MCP servers and register ``mcp.<server>.<tool>`` specs."""
    ts_cfg = getattr(settings, "toolsets", None)
    if isinstance(ts_cfg, ToolsetsSettings) and not ts_cfg.is_toolset_enabled("mcp"):
        return

    bundles = load_mcp_bundles(settings)
    if not bundles:
        return

    for bundle in bundles:
        client = bundle.client
        try:
            await client.connect()
            specs = await client.list_tools()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP server %r setup failed: %s", bundle.server_id, exc)
            try:
                await client.close()
            except Exception:
                pass
            continue

        _ACTIVE.append(client)
        for spec in specs:
            registry.register(spec, overwrite=True)
        logger.info("MCP server %r registered %d tool(s)", bundle.server_id, len(specs))


async def shutdown_mcp_servers() -> None:
    """Terminate subprocess / HTTP sessions for all MCP clients we opened."""
    global _ACTIVE
    for client in _ACTIVE:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MCP client shutdown: %s", exc)
    _ACTIVE.clear()
