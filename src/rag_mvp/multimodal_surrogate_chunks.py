"""Turn MinerU ``content_list`` multimodal blocks into text chunks for ``ainsert_custom_kg``.

No RAG-Anything / graph pipeline — only string surrogates + LightRAG token chunking.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from loguru import logger

# When enable_image_filter marks an image USELESS, llm._filtered_vision_model_func returns this only.
_VISION_SKIPPED_DECORATIVE = "该图片为装饰性图片或无实质内容，已跳过分析。"


def _normalize_caption_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _get_table_body(item: dict[str, Any]) -> Any:
    if item.get("table_body") not in (None, ""):
        return item.get("table_body")
    if item.get("table_data") not in (None, ""):
        return item.get("table_data")
    return item.get("text", "")


def _format_table_body(table_body: Any) -> str:
    """Render table content as Markdown-ish text (list-of-lists → pipe table)."""
    if isinstance(table_body, str):
        return table_body.strip()
    if isinstance(table_body, list):
        if not table_body:
            return ""
        if all(isinstance(row, (list, tuple)) for row in table_body):
            rows = table_body
            rendered = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
            if len(rendered) >= 1:
                column_count = max(len(row) for row in rows)
                sep = "| " + " | ".join(["---"] * column_count) + " |"
                rendered.insert(1, sep)
            return "\n".join(rendered).strip()
        return "\n".join(str(row) for row in table_body).strip()
    return str(table_body).strip()


def _image_surrogate_caption_path(item: dict[str, Any]) -> str:
    """Text-only image surrogate (caption / footnote / file name)."""
    caps = _normalize_caption_list(item.get("image_caption"))
    foots = _normalize_caption_list(item.get("image_footnote"))
    parts: list[str] = ["[Image]"]
    if caps:
        parts.append("Caption: " + " ".join(caps))
    if foots:
        parts.append("Note: " + " ".join(foots))
    rel = item.get("img_path")
    if rel:
        parts.append("File: " + Path(str(rel)).name)
    return "\n".join(parts).strip()


async def _call_vision_image_summary(b64: str, mime: str) -> str:
    """Vision call for ingest surrogate path (respects ``enable_image_filter``)."""
    from rag_mvp.config import settings
    from rag_mvp.llm import _filtered_vision_model_func, vision_model_func

    fn = _filtered_vision_model_func if settings.enable_image_filter else vision_model_func
    return await fn(
        settings.ingest_surrogate_image_vlm_user_prompt,
        system_prompt=settings.ingest_surrogate_image_vlm_system_prompt,
        image_data=b64,
        image_mime=mime,
        max_tokens=settings.ingest_surrogate_image_vlm_max_tokens,
        temperature=0.2,
    )


async def content_item_to_surrogate_text_async(
    item: dict[str, Any],
    *,
    use_vlm_for_images: bool,
    vlm_semaphore: asyncio.Semaphore | None = None,
) -> str:
    """Like :func:`content_item_to_surrogate_text` but optionally appends a VLM summary for ``image`` items."""
    ctype = str(item.get("type") or "unknown").strip()
    if ctype != "image" or not use_vlm_for_images:
        return content_item_to_surrogate_text(item)

    base = _image_surrogate_caption_path(item)
    path_str = item.get("img_path")
    if not path_str:
        return content_item_to_surrogate_text(item)

    p = Path(str(path_str))
    if not p.is_file():
        return content_item_to_surrogate_text(item)

    from rag_mvp.config import settings

    try:
        sz = p.stat().st_size
    except OSError as exc:
        logger.debug("ingest VLM skip image stat failed {}: {}", p.name, exc)
        return base if base else content_item_to_surrogate_text(item)

    if sz > settings.ingest_surrogate_image_vlm_max_bytes:
        logger.debug(
            "ingest VLM skip image too large: {} ({} bytes > {})",
            p.name,
            sz,
            settings.ingest_surrogate_image_vlm_max_bytes,
        )
        return base if base else content_item_to_surrogate_text(item)

    b64 = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    from rag_mvp.llm import image_mime_type_for_suffix

    mime = image_mime_type_for_suffix(p.suffix)

    try:
        if vlm_semaphore is not None:
            async with vlm_semaphore:
                summary = await _call_vision_image_summary(b64, mime)
        else:
            summary = await _call_vision_image_summary(b64, mime)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest VLM image summary failed for {}: {}", p.name, exc)
        return base if base else content_item_to_surrogate_text(item)

    summary = (summary or "").strip()
    if not summary or summary == _VISION_SKIPPED_DECORATIVE:
        if summary == _VISION_SKIPPED_DECORATIVE:
            logger.debug("ingest VLM skip decorative image: {}", p.name)
        return base if base else content_item_to_surrogate_text(item)

    glue = base if base.strip() else "[Image]"
    merged = f"{glue}\nVisual summary: {summary}".strip()
    logger.debug("ingest VLM image summary ok: {}", p.name)
    return merged


def content_item_to_surrogate_text(item: dict[str, Any]) -> str:
    """Map one multimodal ``content_list`` item to a single searchable text blob."""
    ctype = str(item.get("type") or "unknown").strip()

    if ctype == "image":
        return _image_surrogate_caption_path(item)

    if ctype == "table":
        caps = _normalize_caption_list(item.get("table_caption"))
        foots = _normalize_caption_list(item.get("table_footnote"))
        body = _format_table_body(_get_table_body(item))
        parts = ["[Table]"]
        if caps:
            parts.append("Caption: " + " ".join(caps))
        if body:
            parts.append(body)
        if foots:
            parts.append("Note: " + " ".join(foots))
        return "\n".join(parts).strip()

    if ctype == "equation":
        text = str(item.get("text") or "").strip()
        latex = str(item.get("latex") or "").strip()
        eq = str(item.get("equation") or "").strip()
        fmt = str(item.get("text_format") or "").strip()
        body = text or latex or eq
        parts = ["[Equation]"]
        if body:
            parts.append(body)
        if fmt:
            parts.append(f"Format: {fmt}")
        return "\n".join(parts).strip()

    if ctype == "code":
        lang = str(item.get("language") or item.get("lang") or "").strip()
        code = str(item.get("code") or item.get("text") or "").strip()
        head = "[Code]"
        if lang:
            head += f" ({lang})"
        return f"{head}\n{code}".strip() if code else head

    if ctype == "list":
        raw = str(item.get("text") or "").strip()
        items = item.get("list_items")
        if isinstance(items, list) and items:
            lines = []
            for el in items:
                if isinstance(el, dict):
                    lines.append(str(el.get("text") or el.get("content") or el).strip())
                else:
                    lines.append(str(el).strip())
            lines = [x for x in lines if x]
            if lines:
                return "[List]\n" + "\n".join(f"- {line}" for line in lines)
        if raw:
            return f"[List]\n{raw}"
        return "[List]"

    if ctype == "chart":
        caps = _normalize_caption_list(item.get("chart_caption") or item.get("caption"))
        body = str(item.get("text") or item.get("chart_body") or "").strip()
        parts = ["[Chart]"]
        if caps:
            parts.append("Caption: " + " ".join(caps))
        if body:
            parts.append(body)
        return "\n".join(parts).strip()

    # generic / unknown
    skip_keys = {"type", "page_idx", "bbox", "_content_list_index"}
    bits: list[str] = [f"[{ctype}]"]
    for k, v in sorted(item.items(), key=lambda kv: kv[0]):
        if k in skip_keys or v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            continue
        bits.append(f"{k}: {v}")
    return "\n".join(bits).strip()


def _surrogate_text_to_custom_chunks(
    lightrag: Any,
    text: str,
    file_path: str,
    *,
    source_id_prefix: str,
    order_base: int,
) -> list[dict[str, Any]]:
    """Split surrogate text using LightRAG token chunking (same contract as engine)."""
    from lightrag.operate import chunking_by_token_size

    text = text.strip()
    if not text:
        return []
    pieces = chunking_by_token_size(
        lightrag.tokenizer,
        text,
        None,
        False,
        lightrag.chunk_overlap_token_size,
        lightrag.chunk_token_size,
    )
    chunks: list[dict[str, Any]] = []
    for p in pieces:
        content = (p.get("content") or "").strip()
        if not content:
            continue
        chunks.append(
            {
                "content": content,
                "source_id": f"{source_id_prefix}-{len(chunks)}",
                "file_path": file_path,
                "chunk_order_index": order_base + len(chunks),
            }
        )
    return chunks


def multimodal_items_to_custom_chunks(
    lightrag: Any,
    items: list[dict[str, Any]],
    file_path: str,
    *,
    order_base: int,
) -> list[dict[str, Any]]:
    """Convert multimodal MinerU items to ``ainsert_custom_kg`` chunk dicts."""
    out: list[dict[str, Any]] = []
    for mm_idx, item in enumerate(items):
        surrogate = content_item_to_surrogate_text(item)
        if not surrogate.strip():
            continue
        prefix = f"mm{mm_idx}"
        sub = _surrogate_text_to_custom_chunks(
            lightrag,
            surrogate,
            file_path,
            source_id_prefix=prefix,
            order_base=order_base + len(out),
        )
        out.extend(sub)
    return out


async def multimodal_items_to_custom_chunks_async(
    lightrag: Any,
    items: list[dict[str, Any]],
    file_path: str,
    *,
    order_base: int,
    use_vlm_for_images: bool,
) -> list[dict[str, Any]]:
    """Convert multimodal MinerU items to chunks; optional per-image VLM with concurrency limit."""
    from rag_mvp.config import settings

    sem = (
        asyncio.Semaphore(settings.ingest_surrogate_image_vlm_max_concurrency)
        if use_vlm_for_images
        else None
    )
    out: list[dict[str, Any]] = []
    for mm_idx, item in enumerate(items):
        surrogate = await content_item_to_surrogate_text_async(
            item,
            use_vlm_for_images=use_vlm_for_images,
            vlm_semaphore=sem,
        )
        if not surrogate.strip():
            continue
        prefix = f"mm{mm_idx}"
        sub = _surrogate_text_to_custom_chunks(
            lightrag,
            surrogate,
            file_path,
            source_id_prefix=prefix,
            order_base=order_base + len(out),
        )
        out.extend(sub)
    return out


def merge_file_chunks_with_global_indices(
    per_file_chunks: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Prefix ``source_id`` per file index and reassign contiguous ``chunk_order_index``."""
    merged: list[dict[str, Any]] = []
    for file_idx, file_chunks in enumerate(per_file_chunks):
        for ch in file_chunks:
            d = dict(ch)
            sid = str(d.get("source_id") or "x")
            d["source_id"] = f"f{file_idx}-{sid}"
            merged.append(d)
    for i, ch in enumerate(merged):
        ch = dict(merged[i])
        ch["chunk_order_index"] = i
        ch["source_id"] = f"c{i}"
        merged[i] = ch
    return merged
