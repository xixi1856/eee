"""Resolve declared settings into runtime parameters and OpenAI-compatible clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai import OpenAI

from edu_agent.config import EduSettings
from edu_agent.providers.registry import get_provider_spec, resolve_provider_id
from edu_agent.providers.types import ProviderPurpose, ResolvedProviderRuntime

if TYPE_CHECKING:
    from edu_agent.types import AgentConfig


def _credential_for_provider(settings: EduSettings, provider_id: str) -> tuple[str, str | None, str | None]:
    """Return (api_key, base_url_override, default_model_override) from settings.providers.entries."""
    ent = settings.providers.entries.get(provider_id)
    if ent is None:
        return "", None, None
    return ent.api_key, ent.base_url, ent.default_model


def resolve_provider_runtime(
    settings: EduSettings,
    overrides: AgentConfig | None,
    purpose: ProviderPurpose = "main",
) -> ResolvedProviderRuntime:
    """Resolve effective provider, model, key, and URL for *purpose*.

    A1: *subagent* and *auxiliary* use the same credential resolution as *main*
    (purpose parameter reserved for A2/A5 differentiation).
    """
    _ = purpose  # reserved
    declared = settings.agent.provider
    if overrides is not None:
        prov_raw = (getattr(overrides, "provider", None) or "").strip()
        if prov_raw:
            declared = prov_raw
    provider_id = resolve_provider_id(declared)
    spec = get_provider_spec(provider_id)

    cfg_key, cfg_url, cfg_model = _credential_for_provider(settings, provider_id)
    api_key = (cfg_key or "").strip()
    if not api_key and spec.default_api_key_when_unset:
        api_key = spec.default_api_key_when_unset.strip()
    if not api_key:
        env_hint = ", ".join(spec.credential_env_vars) if spec.credential_env_vars else "LLM_API_KEY"
        raise ValueError(
            f"Missing api_key for provider {provider_id!r}. "
            f"Set providers.entries.{provider_id}.api_key in edu_agent.yaml or export one of: {env_hint}."
        )

    base_url = cfg_url if cfg_url else spec.default_base_url
    if spec.default_base_url is None and not cfg_url:
        raise ValueError(
            f"Provider {provider_id!r} requires base_url in edu_agent.yaml "
            f"(providers.entries.{provider_id}.base_url)."
        )

    model = settings.agent.model
    if overrides is not None:
        om = (getattr(overrides, "model", None) or "").strip()
        if om:
            model = om
    if not model and cfg_model:
        model = cfg_model
    if not model and spec.default_model:
        model = spec.default_model

    if not model:
        raise ValueError(f"No model resolved for provider {provider_id!r}.")

    return ResolvedProviderRuntime(
        provider_id=provider_id,
        model=model,
        api_key=api_key,
        base_url=base_url,
        api_mode=spec.api_mode,
        client_kind=spec.client_kind,
        temperature=settings.agent.temperature,
        max_tokens=settings.agent.max_tokens,
    )


def build_openai_client(rt: ResolvedProviderRuntime) -> OpenAI:
    """Construct a synchronous OpenAI-compatible client from resolved runtime."""
    if rt.client_kind != "openai":
        raise ValueError(f"Unsupported client_kind: {rt.client_kind}")
    kwargs: dict = {"api_key": rt.api_key}
    if rt.base_url:
        kwargs["base_url"] = rt.base_url
    return OpenAI(**kwargs)
