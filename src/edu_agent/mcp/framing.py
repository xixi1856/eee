"""Content-Length framed JSON-RPC messages (MCP stdio transport)."""

from __future__ import annotations

import json
from typing import Any

import asyncio


async def read_framed_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one MCP message: headers ending with blank line, then body."""
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line == b"":
            raise EOFError("MCP stream closed before message complete")
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    clen_raw = headers.get("content-length")
    if not clen_raw:
        raise ValueError(f"MCP message missing Content-Length: {headers!r}")
    clen = int(clen_raw)
    body = await reader.readexactly(clen)
    return json.loads(body.decode("utf-8"))


def framed_bytes(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
    return header + raw


async def drain_stderr(proc: asyncio.subprocess.Process) -> None:
    if proc.stderr is None:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
    except Exception:
        return
