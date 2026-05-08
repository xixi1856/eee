"""File I/O tools (sandboxed to output/ directory).

Toolset: files
Tools: write_file, read_file
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from edu_agent.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SCHEMA_WRITE_FILE = {
    "name": "write_file",
    "description": (
        "将文本内容写入本地文件（限 output/ 目录内）。"
        "可用于保存爬取的资讯、生成的报告或整理好的笔记。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于 output/ 目录的文件路径，如 news/2026-05-07.md",
            },
            "content": {"type": "string", "description": "要写入的文本内容"},
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "写入模式：overwrite（覆盖，默认）或 append（追加）",
            },
        },
        "required": ["path", "content"],
    },
}

_SCHEMA_READ_FILE = {
    "name": "read_file",
    "description": (
        "读取 output/ 目录内的本地文件内容。"
        "可用于查看之前保存的资讯或报告。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于 output/ 目录的文件路径",
            },
            "max_chars": {"type": "integer", "description": "返回最大字符数（默认 16000）"},
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------

def _resolve_output_path(path: str, base: str = "output") -> tuple[Any, str | None]:
    """Resolve *path* under *base*, return (resolved_path, error_or_None)."""
    base_path = Path(base).resolve()
    candidate = (base_path / path).resolve()
    if not str(candidate).startswith(str(base_path)):
        return None, f"路径越界，拒绝访问: {path}"
    return candidate, None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_write_file(args: dict, **kw) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return tool_error("缺少必要参数：path")
    mode: str = args.get("mode", "overwrite")
    resolved, err = _resolve_output_path(path)
    if err:
        return tool_error(err)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        if mode == "append":
            resolved.open("a", encoding="utf-8").write(content)
        else:
            resolved.write_text(content, encoding="utf-8")
        size = resolved.stat().st_size
        return tool_result(
            f"已写入 {size} 字节 → {resolved}",
            payload={"path": str(resolved), "bytes": size},
        )
    except OSError as exc:
        return tool_error(str(exc))


def _handle_read_file(args: dict, **kw) -> str:
    path = args.get("path", "")
    if not path:
        return tool_error("缺少必要参数：path")
    max_chars = int(args.get("max_chars", 16000))
    resolved, err = _resolve_output_path(path)
    if err:
        return tool_error(err)
    if not resolved.exists():
        return tool_error(f"文件不存在: {path}")
    try:
        text = resolved.read_text(encoding="utf-8")[:max_chars]
        return tool_result(text)
    except OSError as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="write_file",
    schema=_SCHEMA_WRITE_FILE,
    handler=_handle_write_file,
    toolset="files",
    emoji="📝",
)

registry.register(
    name="read_file",
    schema=_SCHEMA_READ_FILE,
    handler=_handle_read_file,
    toolset="files",
    emoji="📂",
)
