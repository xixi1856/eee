"""Provider spec and resolved runtime types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderPurpose = Literal["main", "subagent", "auxiliary"]


class ProviderSpec(BaseModel):
    """Canonical provider metadata — single source of truth with registry."""

    id: str
    aliases: list[str] = Field(default_factory=list)
    default_base_url: str | None = None
    api_mode: Literal["chat_completions", "responses", "anthropic_messages"] = "chat_completions"
    client_kind: Literal["openai", "anthropic"] = "openai"
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    default_model: str | None = None
    # Env keys to try (in order) when yaml entry has no api_key — filled by registry.merge only.
    credential_env_vars: list[str] = Field(default_factory=list)
    # If still empty after env scan, use this (e.g. local Ollama OpenAI-compatible convention).
    default_api_key_when_unset: str | None = None


class ResolvedProviderRuntime(BaseModel):
    """Resolved, read-only provider connection parameters for one purpose."""

    provider_id: str
    model: str
    api_key: str
    base_url: str | None
    api_mode: str
    client_kind: str
    temperature: float
    max_tokens: int
