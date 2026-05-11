"""File I/O tools (sandboxed to output/ directory).

Toolset: files
Tools: write_file, read_file
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry

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

async def _handle_write_file(args: dict) -> str:
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


async def _handle_read_file(args: dict) -> str:
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

toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_WRITE_FILE["name"],
        description=_SCHEMA_WRITE_FILE["description"],
        input_schema=_SCHEMA_WRITE_FILE["parameters"],
        handler=_handle_write_file,
        toolset="files",
        permissions=[ToolPermission.WRITE],
        approval_required=True,
        emoji="📝",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_READ_FILE["name"],
        description=_SCHEMA_READ_FILE["description"],
        input_schema=_SCHEMA_READ_FILE["parameters"],
        handler=_handle_read_file,
        toolset="files",
        permissions=[ToolPermission.READ],
        emoji="📂",
    )
)

# ---------------------------------------------------------------------------
# Attachment tools (multimodal)
# ---------------------------------------------------------------------------

_SCHEMA_PARSE_ATTACHMENT = {
    "name": "parse_attachment",
    "description": (
        "从用户上传的附件（图片/PDF/DOCX 等）中提取完整文本内容。"
        "与系统自动提供的 800 字符预览不同，此工具会返回文件的完整文本（最多 16000 字符）。"
        "不会将内容存入知识库。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "presigned_url": {
                "type": "string",
                "description": "附件的预签名 URL（由消息中的附件元数据提供）",
            },
            "name": {
                "type": "string",
                "description": "附件文件名（用于记录日志和确定文件类型）",
            },
            "mime_type": {
                "type": "string",
                "description": "附件 MIME 类型，如 application/pdf、image/png 等",
            },
        },
        "required": ["presigned_url", "name"],
    },
}

_SCHEMA_INGEST_ATTACHMENT = {
    "name": "ingest_attachment",
    "description": (
        "将用户上传的附件完整解析并存入个人知识库（RAG 索引）。"
        "此操作是持久化的，之后可通过 search_rag 工具查询文档内容。"
        "**必须在用户明确要求时才调用此工具**。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "presigned_url": {
                "type": "string",
                "description": "附件的预签名 URL",
            },
            "name": {
                "type": "string",
                "description": "附件文件名",
            },
            "mime_type": {
                "type": "string",
                "description": "附件 MIME 类型",
            },
        },
        "required": ["presigned_url", "name"],
    },
}


_MIME_TO_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "text/markdown": ".md",
}


async def _download_to_tempfile(url: str, suffix: str) -> Path | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(resp.content)
                return Path(tmp.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Attachment download failed: %s", exc)
        return None


async def _extract_text_from_file(file_path: Path, mime_type: str, max_chars: int) -> str:
    """Extract text from a local file. Returns the extracted text."""
    try:
        if mime_type in ("text/plain", "text/markdown"):
            return file_path.read_text(errors="replace")[:max_chars]
        if mime_type == "application/pdf":
            from io import BytesIO
            import pypdf
            reader = pypdf.PdfReader(BytesIO(file_path.read_bytes()))
            parts = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(parts)[:max_chars]
        # Fallback: RAGAnything parse_document for DOCX/PPTX/XLSX/images
        from rag_mvp.engine import _build_parser  # type: ignore[import]
        rag = _build_parser()
        out_dir = str(file_path.parent / file_path.stem)
        await rag.parse_document(file_path=str(file_path), output_dir=out_dir)
        out_texts = list(Path(out_dir).rglob("*.txt"))
        text = "\n".join(p.read_text(errors="replace") for p in out_texts)
        return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Text extraction failed: %s", exc)
        return ""


async def _handle_parse_attachment(args: dict) -> str:
    url = args.get("presigned_url", "")
    name = args.get("name", "attachment")
    mime_type = args.get("mime_type", "application/octet-stream")
    if not url:
        return tool_error("缺少必要参数：presigned_url")

    suffix = _MIME_TO_SUFFIX.get(mime_type, "")
    tmp_path = await _download_to_tempfile(url, suffix)
    if tmp_path is None:
        return tool_error(f"无法下载附件：{name}")

    try:
        text = await _extract_text_from_file(tmp_path, mime_type, max_chars=16000)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not text.strip():
        return tool_error(f"无法从 {name} 中提取文本内容（可能是扫描件或加密文档）")

    total = len(text)
    truncated = total >= 16000
    header = f"[文件: {name} | 共 {total} 字符"
    if truncated:
        header += "（已截断至 16000 字符）"
    header += "]"
    return tool_result(f"{header}\n{text}", payload={"name": name, "chars": total})


async def _handle_ingest_attachment(args: dict) -> str:
    url = args.get("presigned_url", "")
    name = args.get("name", "attachment")
    mime_type = args.get("mime_type", "application/octet-stream")
    if not url:
        return tool_error("缺少必要参数：presigned_url")

    suffix = _MIME_TO_SUFFIX.get(mime_type, "")
    tmp_path = await _download_to_tempfile(url, suffix)
    if tmp_path is None:
        return tool_error(f"无法下载附件：{name}")

    try:
        from rag_mvp.engine import ingest_file  # type: ignore[import]
        import asyncio
        await asyncio.to_thread(ingest_file, tmp_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Ingest failed for %s: %s", name, exc)
        return tool_error(f"文档 {name} 存入知识库失败：{exc}")
    finally:
        tmp_path.unlink(missing_ok=True)

    return tool_result(
        f"文档《{name}》已成功存入个人知识库，现在可以通过 search_rag 查询其内容。",
        payload={"name": name, "status": "ingested"},
    )


toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_PARSE_ATTACHMENT["name"],
        description=_SCHEMA_PARSE_ATTACHMENT["description"],
        input_schema=_SCHEMA_PARSE_ATTACHMENT["parameters"],
        handler=_handle_parse_attachment,
        toolset="files",
        permissions=[ToolPermission.READ],
        emoji="📎",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_INGEST_ATTACHMENT["name"],
        description=_SCHEMA_INGEST_ATTACHMENT["description"],
        input_schema=_SCHEMA_INGEST_ATTACHMENT["parameters"],
        handler=_handle_ingest_attachment,
        toolset="files",
        permissions=[ToolPermission.READ, ToolPermission.WRITE],
        approval_required=True,
        emoji="📥",
    )
)

