"""Qwen LLM, Vision LLM, and Embedding functions for RAG-Anything / LightRAG."""

import base64
import contextvars
import logging
from dataclasses import dataclass, field
from pathlib import Path

from openai import AsyncOpenAI
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import EmbeddingFunc
from loguru import logger

from .config import settings

# ---------------------------------------------------------------------------
# Inject source context into LightRAG's rate-limit log lines.
# LightRAG logs the error BEFORE re-raising, so we cannot add context
# after the fact.  Instead we use a contextvar + logging.Filter so that
# the "lightrag" logger automatically prepends the calling role.
# ---------------------------------------------------------------------------

_llm_role: contextvars.ContextVar[str] = contextvars.ContextVar("_llm_role", default="")


class _RolePrefixFilter(logging.Filter):
    """Prepend the current LLM role to rate-limit error messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        role = _llm_role.get("")
        if role and "Rate Limit" in record.getMessage():
            record.msg = f"[{role}] {record.msg}"
            record.args = ()  # args already interpolated above via getMessage()
        return True


logging.getLogger("lightrag").addFilter(_RolePrefixFilter())


def image_mime_type_for_suffix(suffix: str) -> str:
    """Return an IANA image MIME type for a file suffix (e.g. ``.png`` → ``image/png``)."""
    s = (suffix or "").lower()
    if s in (".jpg", ".jpeg"):
        return "image/jpeg"
    if s == ".png":
        return "image/png"
    return "image/jpeg"


def build_data_uri_from_image_path(path: Path | str) -> str:
    """Read an image file and return a ``data:{mime};base64,...`` URL for multimodal APIs."""
    p = Path(path)
    mime = image_mime_type_for_suffix(p.suffix)
    raw = p.read_bytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def llm_model_func(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list = [],
    **kwargs,
) -> str:
    """Text LLM backed by qwen-plus (OpenAI-compatible, async)."""
    kwargs.setdefault("max_tokens", settings.llm_max_tokens)
    kwargs.setdefault("temperature", settings.llm_temperature)
    token = _llm_role.set(f"chat/{settings.llm_model}")
    try:
        return await openai_complete_if_cache(
            settings.llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            **kwargs,
        )
    finally:
        _llm_role.reset(token)


async def vision_model_func(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list = [],
    image_data: str | None = None,
    messages: list | None = None,
    image_mime: str | None = None,
    **kwargs,
) -> str:
    """Vision LLM backed by qwen-vl-max (async).

    Three calling conventions from RAG-Anything:
    1. messages already constructed (aquery_vlm_enhanced multimodal path).
    2. image_data as base64 string (ImageModalProcessor path).
    3. Text-only fallback -> llm_model_func.
    """
    kwargs.setdefault("max_tokens", settings.llm_max_tokens)
    _safe = {k: v for k, v in kwargs.items() if k in ("max_tokens", "temperature")}

    if messages is not None:
        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        token = _llm_role.set(f"vision/{settings.vision_model}")
        try:
            resp = await client.chat.completions.create(
                model=settings.vision_model,
                messages=messages,
                **_safe,
            )
        finally:
            _llm_role.reset(token)
        return resp.choices[0].message.content or ""

    if image_data is not None:
        mime = image_mime or "image/jpeg"
        content_parts: list = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
        ]
        msg_list: list = []
        if system_prompt:
            msg_list.append({"role": "system", "content": system_prompt})
        msg_list.append({"role": "user", "content": content_parts})

        client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        token = _llm_role.set(f"vision/{settings.vision_model}")
        try:
            resp = await client.chat.completions.create(
                model=settings.vision_model,
                messages=msg_list,
                **_safe,
            )
        finally:
            _llm_role.reset(token)
        return resp.choices[0].message.content or ""

    return await llm_model_func(prompt, system_prompt, history_messages, **kwargs)


# ---------------------------------------------------------------------------
# Image filter statistics (accumulated in-process for the current session)
# ---------------------------------------------------------------------------

@dataclass
class _ImageFilterStats:
    total: int = 0
    filtered: int = 0
    errors: int = 0
    _counts: dict = field(default_factory=dict)  # filename -> "USEFUL"|"USELESS"


_filter_stats = _ImageFilterStats()


def get_image_filter_stats() -> dict:
    """Return a snapshot of image filtering statistics for the current session."""
    return {
        "total_images": _filter_stats.total,
        "useful_images": _filter_stats.total - _filter_stats.filtered - _filter_stats.errors,
        "filtered_images": _filter_stats.filtered,
        "filter_errors": _filter_stats.errors,
    }


async def _call_vision_raw(
    prompt: str,
    image_data: str,
    system_prompt: str | None = None,
    max_tokens: int = 20,
    image_mime: str | None = None,
) -> str:
    """Low-level vision call with explicit token limit (used for cheap filter checks)."""
    mime = image_mime or "image/jpeg"
    content_parts: list = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
    ]
    msg_list: list = []
    if system_prompt:
        msg_list.append({"role": "system", "content": system_prompt})
    msg_list.append({"role": "user", "content": content_parts})

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    token = _llm_role.set(f"vision-filter/{settings.vision_model}")
    try:
        resp = await client.chat.completions.create(
            model=settings.vision_model,
            messages=msg_list,
            max_tokens=max_tokens,
            temperature=0.0,
        )
    finally:
        _llm_role.reset(token)
    return (resp.choices[0].message.content or "").strip().upper()


async def _filtered_vision_model_func(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list = [],
    image_data: str | None = None,
    messages: list | None = None,
    image_mime: str | None = None,
    **kwargs,
) -> str:
    """vision_model_func wrapper that skips useless images via a cheap binary pre-check.

    When ``settings.enable_image_filter`` is True and ``image_data`` is provided,
    a short binary prompt is sent first (max_tokens=20).  If the response contains
    "USELESS" the image is marked as filtered and a placeholder description is
    returned without running the expensive full analysis call.
    """
    if settings.enable_image_filter and image_data is not None and messages is None:
        _filter_stats.total += 1
        try:
            verdict = await _call_vision_raw(
                prompt=settings.image_filter_prompt,
                image_data=image_data,
                max_tokens=20,
                image_mime=image_mime,
            )
        except Exception as exc:  # noqa: BLE001
            _filter_stats.errors += 1
            logger.warning(f"[image-filter] 过滤检查失败，默认保留图片: {exc}")
            verdict = "USEFUL"

        if "USELESS" in verdict:
            _filter_stats.filtered += 1
            stats = get_image_filter_stats()
            logger.debug(
                f"[image-filter] 跳过装饰性图片 (已过滤 {stats['filtered']}/{stats['total']})"
            )
            # Return a placeholder so rag-anything still gets a valid string.
            # The chunk will have minimal content and low retrieval weight.
            return "该图片为装饰性图片或无实质内容，已跳过分析。"
        else:
            logger.debug("[image-filter] 图片通过筛选，进行完整分析")

    return await vision_model_func(
        prompt=prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        image_data=image_data,
        messages=messages,
        image_mime=image_mime,
        **kwargs,
    )


async def _embedding_func_with_label(texts, **kwargs):
    """Wrapper that sets the LLM role contextvar for rate-limit log labeling."""
    token = _llm_role.set(f"embedding/{settings.embedding_model}")
    try:
        return await openai_embed.func(
            texts,
            model=settings.embedding_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            **kwargs,
        )
    finally:
        _llm_role.reset(token)


# openai_embed is an EmbeddingFunc instance; use .func to get the raw async callable.
embedding_func = EmbeddingFunc(
    embedding_dim=settings.embedding_dim,
    max_token_size=settings.embedding_max_tokens,
    func=_embedding_func_with_label,
)
