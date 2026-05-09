"""MCP client abstractions (stdio JSON-RPC + HTTP JSON-RPC)."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from edu_agent.mcp.framing import drain_stderr, framed_bytes, read_framed_json
from edu_agent.toolsets.models import ToolPermission, ToolSpec

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


def _stdio_tool_handler_factory(client: "StdioMCPClient", inner_name: str):
    async def _handler(args: dict[str, Any]) -> str:
        return await client.call_tool(inner_name, args)

    return _handler


def _http_tool_handler_factory(client: "HttpMCPClient", inner_name: str):
    async def _handler(args: dict[str, Any]) -> str:
        return await client.call_tool(inner_name, args)

    return _handler


class MCPClient(ABC):
    server_id: str

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        raise NotImplementedError


class StdioMCPClient(MCPClient):
    """MCP over stdio (Content-Length framed JSON-RPC)."""

    def __init__(self, command: str, args: list[str] | None, server_id: str) -> None:
        self.command = command
        self.args = list(args or [])
        self.server_id = server_id
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def connect(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdin and self._proc.stdout
        self._stderr_task = asyncio.create_task(drain_stderr(self._proc))

        init_id = self._next_id
        self._next_id += 1
        await self._send_raw(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "edu_agent", "version": "0.1.0"},
                },
            }
        )
        init_resp = await self._read_matching_id(init_id)
        if "error" in init_resp:
            raise RuntimeError(f"MCP initialize failed: {init_resp['error']}")
        await self._send_raw(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )

    async def close(self) -> None:
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
                if hasattr(self._proc.stdin, "wait_closed"):
                    await self._proc.stdin.wait_closed()
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

    async def _send_raw(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP client not connected")
        self._proc.stdin.write(framed_bytes(payload))
        await self._proc.stdin.drain()

    async def _read_matching_id(self, req_id: int) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("MCP client not connected")
        reader = self._proc.stdout
        stray = 0
        while True:
            msg = await read_framed_json(reader)
            if msg.get("method") == "notifications/message":
                continue
            if "id" in msg:
                if msg["id"] == req_id:
                    return msg
                logger.warning("MCP stray json-rpc id=%s (expected %s)", msg.get("id"), req_id)
                stray += 1
                if stray > 64:
                    raise RuntimeError("MCP protocol: too many stray responses while waiting for id")
                continue
            stray += 1
            if stray > 64:
                raise RuntimeError("MCP protocol: too many non-id messages while waiting for id")

    async def list_tools(self) -> list[ToolSpec]:
        async with self._lock:
            await self.connect()
            rid = self._next_id
            self._next_id += 1
            await self._send_raw(
                {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}}
            )
            resp = await self._read_matching_id(rid)
        if "error" in resp:
            raise RuntimeError(f"tools/list failed: {resp['error']}")
        result = resp.get("result") or {}
        raw_tools = result.get("tools") or []
        out: list[ToolSpec] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            inner = str(t.get("name") or "").strip()
            if not inner:
                continue
            full_name = f"mcp.{self.server_id}.{inner}"
            desc = str(t.get("description") or "")
            schema = t.get("inputSchema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": {"_": schema}}

            out.append(
                ToolSpec(
                    name=full_name,
                    description=desc,
                    input_schema=schema,
                    handler=_stdio_tool_handler_factory(self, inner),
                    toolset="mcp",
                    permissions=[ToolPermission.EXTERNAL],
                    emoji="🔌",
                )
            )
        return out

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        async with self._lock:
            await self.connect()
            rid = self._next_id
            self._next_id += 1
            await self._send_raw(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                }
            )
            resp = await self._read_matching_id(rid)
        if "error" in resp:
            err = resp["error"]
            text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return f'{{"error": {json.dumps(text, ensure_ascii=False)}}}'
        result = resp.get("result") or {}
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            return json.dumps({"result": "\n".join(parts)}, ensure_ascii=False)
        if isinstance(result, dict) and "content" not in result:
            return json.dumps(result, ensure_ascii=False, default=str)
        return json.dumps({"result": str(result)}, ensure_ascii=False)


class HttpMCPClient(MCPClient):
    """Stateless JSON-RPC POST to an HTTP MCP gateway (best-effort)."""

    def __init__(self, base_url: str, server_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.server_id = server_id
        self._http: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
        rid = 1
        r = await self._http.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": rid,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "edu_agent", "version": "0.1.0"},
                },
            },
        )
        r.raise_for_status()
        await self._http.post(
            "/",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def list_tools(self) -> list[ToolSpec]:
        await self.connect()
        assert self._http is not None
        r = await self._http.post(
            "/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        r.raise_for_status()
        resp = r.json()
        if "error" in resp:
            raise RuntimeError(f"tools/list failed: {resp['error']}")
        result = resp.get("result") or {}
        raw_tools = result.get("tools") or []
        out: list[ToolSpec] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            inner = str(t.get("name") or "").strip()
            if not inner:
                continue
            full_name = f"mcp.{self.server_id}.{inner}"
            desc = str(t.get("description") or "")
            schema = t.get("inputSchema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": {"_": schema}}

            out.append(
                ToolSpec(
                    name=full_name,
                    description=desc,
                    input_schema=schema,
                    handler=_http_tool_handler_factory(self, inner),
                    toolset="mcp",
                    permissions=[ToolPermission.EXTERNAL],
                    emoji="🔌",
                )
            )
        return out

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        await self.connect()
        assert self._http is not None
        r = await self._http.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        r.raise_for_status()
        resp = r.json()
        if "error" in resp:
            err = resp["error"]
            text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return f'{{"error": {json.dumps(text, ensure_ascii=False)}}}'
        result = resp.get("result") or {}
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            return json.dumps({"result": "\n".join(parts)}, ensure_ascii=False)
        return json.dumps({"result": str(result)}, ensure_ascii=False)
