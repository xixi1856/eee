"""Load MCP server definitions from settings (A4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from edu_agent.config import EduSettings
from edu_agent.mcp.client import HttpMCPClient, MCPClient, StdioMCPClient


def _slug(server_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", server_id.strip()).strip("_").lower()
    return s or "server"


@dataclass
class MCPBundle:
    server_id: str
    client: MCPClient


def load_mcp_bundles(settings: EduSettings) -> list[MCPBundle]:
    """Build MCP clients from ``settings.tools.mcp_servers`` (not yet connected)."""
    raw = settings.tools.mcp_servers
    if not raw:
        return []
    bundles: list[MCPBundle] = []
    for i, ent in enumerate(raw):
        if not isinstance(ent, dict):
            continue
        uri = str(ent.get("uri") or ent.get("type") or "").lower()
        sid_raw = str(ent.get("name") or ent.get("id") or "").strip()
        sid = _slug(sid_raw) if sid_raw else f"s{i}"
        if uri == "stdio":
            cmd = str(ent.get("command") or "").strip()
            if not cmd:
                continue
            args = [str(a) for a in (ent.get("args") or [])]
            bundles.append(MCPBundle(sid, StdioMCPClient(cmd, args, sid)))
            continue
        if uri in ("http", "https"):
            url = str(ent.get("url") or "").strip()
            if not url:
                continue
            if uri == "https" and not url.lower().startswith("http"):
                url = "https://" + url
            elif uri == "http" and not url.lower().startswith("http"):
                url = "http://" + url
            bundles.append(MCPBundle(sid, HttpMCPClient(url, sid)))
    return bundles
