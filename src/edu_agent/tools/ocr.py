"""OCR / document-parsing tool (A4 async)."""

from __future__ import annotations

import logging

from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "parse_document",
    "description": (
        "解析 PDF、图片或 Word 文档（使用 MinerU），将其转换为可检索的 Markdown 格式。"
        "在导入知识库之前需要先解析文档。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "待解析的文件路径或目录路径",
            },
        },
        "required": ["path"],
    },
}


async def _handle_parse_document(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return tool_error("缺少必要参数：path")
    try:
        from pathlib import Path as _Path

        from rag_mvp.engine import parse_file, parse_folder

        target = _Path(path)
        if target.is_dir():
            parse_folder(target)
            summary = f"目录 '{path}' 下的文档已全部解析完毕。"
        else:
            parse_file(target)
            summary = f"文档 '{path}' 已解析完毕。"
        return tool_result(summary)
    except Exception as exc:
        logger.error("parse_document failed: %s", exc)
        return tool_error(str(exc))


toolset_registry.register(
    ToolSpec(
        name=SCHEMA["name"],
        description=SCHEMA["description"],
        input_schema=SCHEMA["parameters"],
        handler=_handle_parse_document,
        toolset="ocr",
        permissions=[ToolPermission.READ, ToolPermission.EXTERNAL],
        emoji="📄",
    )
)
