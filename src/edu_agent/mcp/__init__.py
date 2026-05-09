"""MCP client integration (A4)."""

from edu_agent.mcp.client import HttpMCPClient, MCPClient, StdioMCPClient
from edu_agent.mcp.integration import register_mcp_servers, shutdown_mcp_servers
from edu_agent.mcp.loader import MCPBundle, load_mcp_bundles

__all__ = [
    "HttpMCPClient",
    "MCPBundle",
    "MCPClient",
    "StdioMCPClient",
    "load_mcp_bundles",
    "register_mcp_servers",
    "shutdown_mcp_servers",
]
