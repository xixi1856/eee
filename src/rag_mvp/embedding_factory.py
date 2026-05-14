"""Single factory for LightRAG ``EmbeddingFunc`` (Ollama vs OpenAI-compatible APIs)."""

from __future__ import annotations

from lightrag.llm.ollama import ollama_embed
from lightrag.llm.openai import openai_embed
from lightrag.utils import EmbeddingFunc

from .config import settings
from .http_env import ensure_loopback_bypass_http_proxy

ensure_loopback_bypass_http_proxy()


def _effective_embedding_base_url() -> str:
    u = (settings.embedding_base_url or settings.llm_base_url or "").strip()
    return u.rstrip("/")


def _effective_embedding_api_key() -> str:
    return (settings.embedding_api_key or settings.llm_api_key or "").strip()


async def _ollama_embedding_func_with_label(texts: list[str], **kwargs):
    """Ollama embeddings via LightRAG's ``ollama_embed.func`` (use .func to avoid double-wrap)."""
    from .llm import _llm_role

    token = _llm_role.set(f"embedding/{settings.embedding_model}")
    try:
        key = settings.ollama_api_key.strip() or None
        return await ollama_embed.func(
            texts,
            embed_model=settings.embedding_model,
            host=settings.ollama_base_url.rstrip("/"),
            api_key=key,
            **kwargs,
        )
    finally:
        _llm_role.reset(token)


async def _openai_compatible_embedding_func_with_label(texts: list[str], **kwargs):
    """OpenAI-compatible ``/v1/embeddings`` (DashScope compatible-mode, OpenAI, etc.)."""
    from .llm import _llm_role

    base = _effective_embedding_base_url()
    key = _effective_embedding_api_key() or None
    token = _llm_role.set(f"embedding/{settings.embedding_model}")
    try:
        # Explicitly request EMBEDDING_DIM dimensions so the API output always matches the
        # configured vector size (e.g. text-embedding-v4 defaults to 1536d but we need 1024d).
        # openai_embed.func accepts `embedding_dim` and internally passes it as `dimensions` to the API.
        return await openai_embed.func(
            texts,
            model=settings.embedding_model,
            base_url=base or None,
            api_key=key,
            embedding_dim=settings.embedding_dim,
            **kwargs,
        )
    finally:
        _llm_role.reset(token)


def build_embedding_func() -> EmbeddingFunc:
    """Return a new ``EmbeddingFunc`` bound to current settings (fresh LightRAG worker queues)."""
    if settings.embedding_mode == "openai_compatible":
        fn = _openai_compatible_embedding_func_with_label
    else:
        fn = _ollama_embedding_func_with_label
    return EmbeddingFunc(
        embedding_dim=settings.embedding_dim,
        max_token_size=settings.embedding_max_tokens,
        func=fn,
        send_dimensions=False,
    )
