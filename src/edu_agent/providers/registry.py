"""Canonical provider registry — no provider branching outside this module + runtime."""

from __future__ import annotations

import os
from typing import Any

from edu_agent.providers.types import ProviderSpec

_REGISTRY: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        aliases=["gpt"],
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        credential_env_vars=["OPENAI_API_KEY", "LLM_API_KEY"],
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        aliases=[],
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        credential_env_vars=["DEEPSEEK_API_KEY", "LLM_API_KEY"],
    ),
    "ollama": ProviderSpec(
        id="ollama",
        aliases=[],
        default_base_url="http://127.0.0.1:11434/v1",
        default_model="llama3.2",
        credential_env_vars=["OLLAMA_API_KEY", "LLM_API_KEY"],
        default_api_key_when_unset="ollama",
    ),
    "dashscope": ProviderSpec(
        id="dashscope",
        aliases=["qwen", "tongyi"],
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus-2025-04-28",
        credential_env_vars=["LLM_API_KEY", "DASHSCOPE_API_KEY"],
    ),
    "openai_compatible": ProviderSpec(
        id="openai_compatible",
        aliases=["compatible"],
        default_base_url=None,
        default_model=None,
        credential_env_vars=["LLM_API_KEY", "OPENAI_API_KEY"],
    ),
}


def merge_env_credentials_into_provider_entry(provider_id: str, entry: dict[str, Any]) -> None:
    """Fill missing *api_key* on a raw yaml/env merge dict using registry-defined env chain.

    Called only from ``config_loader`` when assembling declarative config — keeps
    provider-specific env naming out of the loader.
    """
    spec = _REGISTRY[resolve_provider_id(provider_id)]
    if (entry.get("api_key") or "").strip():
        return
    for env_name in spec.credential_env_vars:
        val = os.environ.get(env_name)
        if val:
            entry["api_key"] = val
            return
    if spec.default_api_key_when_unset:
        entry["api_key"] = spec.default_api_key_when_unset


def _alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for spec in _REGISTRY.values():
        idx[spec.id.lower()] = spec.id
        for a in spec.aliases:
            idx[a.lower()] = spec.id
    return idx


_ALIAS_TO_ID = _alias_index()


def resolve_provider_id(name: str) -> str:
    """Map user/provider string to canonical registry id."""
    key = name.strip().lower()
    if key in _REGISTRY:
        return key
    if key in _ALIAS_TO_ID:
        return _ALIAS_TO_ID[key]
    raise ValueError(f"Unknown provider: {name!r}. Known: {sorted(_REGISTRY)}")


def get_provider_spec(provider_id: str) -> ProviderSpec:
    """Return spec for canonical provider id or alias."""
    return _REGISTRY[resolve_provider_id(provider_id)]


def list_provider_ids() -> list[str]:
    return sorted(_REGISTRY)
