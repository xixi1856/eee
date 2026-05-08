"""OCR / document-parsing tool (MinerU pipeline).

Toolset: ocr
"""

from __future__ import annotations

import logging

from edu_agent.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema (inner format — no outer "type":"function" wrapper)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_parse_document(args: dict, **kw) -> str:
    path = args.get("path", "")
    if not path:
        return tool_error("缺少必要参数：path")
    try:
        from pathlib import Path as _Path

        from rag_mvp.engine import parse_file, parse_folder  # lazy import

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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="parse_document",
    schema=SCHEMA,
    handler=_handle_parse_document,
    toolset="ocr",
    emoji="📄",
)
