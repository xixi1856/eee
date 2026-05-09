"""Load EduSettings from edu_agent.yaml + .env — entry layer only."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from edu_agent.config import (
    AgentDefaults,
    EduSettings,
    ProviderCredentials,
    ProvidersSettings,
    RuntimeSettings,
    ToolsSettings,
    ToolsetsSettings,
)


_ENV_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def resolve_env_vars(value: Any) -> Any:
    """Recursively substitute ``${VAR}`` placeholders using *os.environ*."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")

        return _ENV_PLACEHOLDER.sub(repl, value)
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _provider_entries_from_raw(providers_block: Any) -> dict[str, Any]:
    """Merge ``providers.entries`` with top-level provider maps from yaml."""
    if not isinstance(providers_block, dict):
        return {}
    out: dict[str, Any] = {}
    inner = providers_block.get("entries")
    if isinstance(inner, dict):
        for k, v in inner.items():
            if isinstance(v, dict):
                out[str(k).lower()] = v
    for k, v in providers_block.items():
        if k == "entries" or not isinstance(v, dict):
            continue
        kid = str(k).lower()
        out.setdefault(kid, v)
    return out


def _apply_legacy_env_into_data(data: dict[str, Any]) -> None:
    """Merge first-party dotenv keys (LLM_*, TAVILY_*, …) into *data* when yaml left blanks.

    This is **not** ``rag_mvp.config`` fallback: values become part of ``EduSettings`` only.
    Env names stay centralized for existing deployments that relied on ``.env`` before
    ``edu_agent.yaml`` existed.
    """
    from edu_agent.providers.registry import merge_env_credentials_into_provider_entry

    agent = data.setdefault("agent", {})
    if not (agent.get("model") or "").strip() and os.environ.get("LLM_MODEL"):
        agent["model"] = os.environ["LLM_MODEL"]
    prov_block = data.get("providers")
    if not isinstance(prov_block, dict):
        prov_block = {}
    merged = _provider_entries_from_raw(prov_block)
    # Pick active provider from yaml or default dashscope
    pid = str(agent.get("provider") or "dashscope").lower()
    try:
        from edu_agent.providers.registry import resolve_provider_id

        pid = resolve_provider_id(pid)
    except ValueError:
        pid = "dashscope"
    entry = merged.setdefault(pid, {})
    merge_env_credentials_into_provider_entry(pid, entry)
    if not entry.get("base_url") and os.environ.get("LLM_BASE_URL"):
        entry["base_url"] = os.environ["LLM_BASE_URL"]
    data["providers"] = {"entries": merged}
    tools = data.setdefault("tools", {})
    if not (tools.get("tavily_api_key") or "").strip() and os.environ.get("TAVILY_API_KEY"):
        tools["tavily_api_key"] = os.environ["TAVILY_API_KEY"]
    if not (tools.get("http_proxy") or "").strip() and os.environ.get("HTTP_PROXY"):
        tools["http_proxy"] = os.environ["HTTP_PROXY"]
    if not (tools.get("ollama_api_key") or "").strip() and os.environ.get("OLLAMA_API_KEY"):
        tools["ollama_api_key"] = os.environ["OLLAMA_API_KEY"]


def _normalize_paths_in_agent(agent: dict[str, Any]) -> None:
    if "workspace" in agent and agent["workspace"] is not None:
        agent["workspace"] = Path(agent["workspace"])


def _normalize_provider_entries(entries: dict[str, Any]) -> dict[str, ProviderCredentials]:
    out: dict[str, ProviderCredentials] = {}
    for k, v in entries.items():
        if not isinstance(v, dict):
            continue
        kid = str(k).lower()
        out[kid] = ProviderCredentials.model_validate(v)
    return out


def load_settings_from_file(path: Path) -> EduSettings:
    """Load settings from a single yaml file (after dotenv)."""
    load_dotenv()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw = resolve_env_vars(raw)
    _apply_legacy_env_into_data(raw)
    agent_dict = dict(AgentDefaults().model_dump())
    agent_dict.update(raw.get("agent") or {})
    _normalize_paths_in_agent(agent_dict)
    agent = AgentDefaults.model_validate(agent_dict)

    prov_raw = _provider_entries_from_raw(raw.get("providers") or {})
    providers = ProvidersSettings(entries=_normalize_provider_entries(prov_raw))

    tools = ToolsSettings.model_validate({**ToolsSettings().model_dump(), **(raw.get("tools") or {})})
    runtime = RuntimeSettings.model_validate(
        {**RuntimeSettings().model_dump(), **(raw.get("runtime") or {})}
    )
    toolsets = ToolsetsSettings.from_raw(raw.get("toolsets") or {})
    return EduSettings(
        agent=agent,
        providers=providers,
        tools=tools,
        runtime=runtime,
        toolsets=toolsets,
    )


def load_settings(config_path: Path | None = None) -> EduSettings:
    """Load EduSettings: optional yaml + .env + legacy env merge.

    If *config_path* is given, load that file. Otherwise look for ``edu_agent.yaml``
    in the current working directory.
    """
    load_dotenv()
    path = config_path or (Path.cwd() / "edu_agent.yaml")
    if path.is_file():
        return load_settings_from_file(path)
    # Defaults only + env merge into a synthetic dict
    data: dict[str, Any] = {
        "agent": AgentDefaults().model_dump(),
        "providers": {"entries": {}},
        "tools": ToolsSettings().model_dump(),
        "runtime": RuntimeSettings().model_dump(),
        "toolsets": {},
    }
    _apply_legacy_env_into_data(data)
    agent_dict = data["agent"]
    _normalize_paths_in_agent(agent_dict)
    agent = AgentDefaults.model_validate(agent_dict)
    prov_raw = _provider_entries_from_raw(data.get("providers") or {})
    providers = ProvidersSettings(entries=_normalize_provider_entries(prov_raw))
    tools = ToolsSettings.model_validate(data.get("tools") or {})
    runtime = RuntimeSettings.model_validate(data.get("runtime") or {})
    toolsets = ToolsetsSettings.from_raw(data.get("toolsets") or {})
    return EduSettings(
        agent=agent,
        providers=providers,
        tools=tools,
        runtime=runtime,
        toolsets=toolsets,
    )
