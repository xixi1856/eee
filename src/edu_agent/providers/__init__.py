"""Provider registry, resolution, and OpenAI-compatible client helpers."""

from edu_agent.providers.registry import (
    get_provider_spec,
    list_provider_ids,
    merge_env_credentials_into_provider_entry,
    resolve_provider_id,
)
from edu_agent.providers.runtime import build_openai_client, resolve_provider_runtime
from edu_agent.providers.types import ProviderPurpose, ProviderSpec, ResolvedProviderRuntime

__all__ = [
    "ProviderPurpose",
    "ProviderSpec",
    "ResolvedProviderRuntime",
    "build_openai_client",
    "get_provider_spec",
    "list_provider_ids",
    "merge_env_credentials_into_provider_entry",
    "resolve_provider_id",
    "resolve_provider_runtime",
]
