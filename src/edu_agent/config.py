"""EduAgent root settings schema (declarative only — no import-time loading)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentDefaults(BaseModel):
    """Default agent behaviour and workspace layout (global defaults)."""

    workspace: Path = Path(".")
    model: str = "qwen-plus-2025-04-28"
    provider: str = "dashscope"
    temperature: float = 0.1
    max_tokens: int = 4096
    max_iterations: int = 20
    skills_dir: str = "skills"


class ProviderCredentials(BaseModel):
    """Per-provider credential and endpoint overrides."""

    api_key: str = ""
    base_url: str | None = None
    default_model: str | None = None


class ProvidersSettings(BaseModel):
    """Map canonical provider id → credentials (declared in yaml / env)."""

    entries: dict[str, ProviderCredentials] = Field(default_factory=dict)


class ToolsSettings(BaseModel):
    """Tool-related configuration (search, eval aux, MCP placeholders)."""

    tavily_api_key: str = ""
    http_proxy: str = ""
    ollama_api_key: str = ""
    # A4 placeholders — keep keys so schema stays stable
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettings(BaseModel):
    """Process-level runtime flags (not session state)."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    default_timezone: str = "UTC"
    env: Literal["dev", "staging", "prod"] = "dev"
    # A5 placeholders
    gateway: dict[str, Any] = Field(default_factory=dict)
    channels: dict[str, Any] = Field(default_factory=dict)


class EduSettings(BaseModel):
    """Root EduAgent configuration — loaded only via config_loader.load_settings()."""

    agent: AgentDefaults = Field(default_factory=AgentDefaults)
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
