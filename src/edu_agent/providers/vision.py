"""Runtime vision capability detection for LLM providers.

Strategy:
- Ollama: POST {base_url}/api/show and check ``capabilities`` array for "vision".
- Others: regex match on model name.
- Results are cached in-process per ``{base_url}::{model}`` key.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edu_agent.providers.types import ResolvedProviderRuntime

logger = logging.getLogger(__name__)

# Patterns that strongly indicate vision support in model names (case-insensitive).
_VISION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"gpt-4o", re.IGNORECASE),
    re.compile(r"gpt-4-turbo", re.IGNORECASE),
    re.compile(r"gpt-4-vision", re.IGNORECASE),
    re.compile(r"claude-3", re.IGNORECASE),
    re.compile(r"claude-3\.5", re.IGNORECASE),
    re.compile(r"qwen.*vl", re.IGNORECASE),
    re.compile(r"qwen2.*vl", re.IGNORECASE),
    re.compile(r"\bllava\b", re.IGNORECASE),
    re.compile(r"bakllava", re.IGNORECASE),
    re.compile(r"minicpm[-_]?v", re.IGNORECASE),
    re.compile(r"moondream", re.IGNORECASE),
    re.compile(r"gemini.*pro.*vision", re.IGNORECASE),
    re.compile(r"gemini-.*-vision", re.IGNORECASE),
    re.compile(r"gemini-1\.5", re.IGNORECASE),
    re.compile(r"gemini-2", re.IGNORECASE),
    re.compile(r"pixtral", re.IGNORECASE),
    re.compile(r"internvl", re.IGNORECASE),
    re.compile(r"cogvlm", re.IGNORECASE),
    re.compile(r"phi-3.*vision", re.IGNORECASE),
    re.compile(r"deepseek.*vl", re.IGNORECASE),
]

_vision_cache: dict[str, bool] = {}


def _model_name_supports_vision(model: str) -> bool:
    return any(p.search(model) for p in _VISION_PATTERNS)


async def _check_ollama_vision(base_url: str, model: str) -> bool | None:
    """Return True/False if Ollama /api/show answers; None on any error."""
    try:
        import httpx

        # Derive root URL (strip /v1 suffix that OpenAI-compat endpoint adds)
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{root}/api/show", json={"name": model})
            if resp.status_code != 200:
                return None
            data = resp.json()
            caps = data.get("capabilities") or []
            if isinstance(caps, list):
                return "vision" in caps
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ollama vision check failed for %s: %s", model, exc)
        return None


async def detect_vision_support(rt: "ResolvedProviderRuntime") -> bool:
    """Return True if the resolved provider/model supports vision (image inputs).

    Results are cached for the lifetime of the process.
    """
    cache_key = f"{rt.base_url or ''}::{rt.model}"
    if cache_key in _vision_cache:
        return _vision_cache[cache_key]

    result: bool

    base_url = rt.base_url or ""
    # Ollama detection: call /api/show for authoritative capabilities
    if "11434" in base_url or rt.provider_id == "ollama":
        ollama_result = await _check_ollama_vision(base_url, rt.model)
        if ollama_result is not None:
            result = ollama_result
        else:
            # Fallback to name matching
            result = _model_name_supports_vision(rt.model)
    else:
        result = _model_name_supports_vision(rt.model)

    logger.debug("Vision support for %r (%s): %s", rt.model, rt.provider_id, result)
    _vision_cache[cache_key] = result
    return result
