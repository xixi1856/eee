"""EduAgent root settings schema (declarative only — no import-time loading)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolsetToggle(BaseModel):
    enabled: bool = True


class ToolsetsSettings(BaseModel):
    """YAML `toolsets:` block — keys are toolset ids; values are bool or {enabled: bool}."""

    entries: dict[str, ToolsetToggle] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> ToolsetsSettings:
        if not isinstance(raw, dict):
            return cls()
        entries: dict[str, ToolsetToggle] = {}
        for k, v in raw.items():
            key = str(k).lower()
            if isinstance(v, bool):
                entries[key] = ToolsetToggle(enabled=v)
            elif isinstance(v, dict):
                entries[key] = ToolsetToggle(enabled=bool(v.get("enabled", True)))
            else:
                entries[key] = ToolsetToggle(enabled=True)
        return cls(entries=entries)

    def is_toolset_enabled(self, toolset: str) -> bool:
        tid = toolset.lower()
        ent = self.entries.get(tid)
        if ent is None:
            return True
        return ent.enabled


class AgentDefaults(BaseModel):
    """Default agent behaviour and workspace layout (global defaults)."""

    workspace: Path = Path(".")
    model: str = "qwen-plus-2025-04-28"
    provider: str = "dashscope"
    temperature: float = 0.1
    max_tokens: int = 4096
    max_iterations: int = 20
    skills_dir: str = "skills"
    tool_timeout_sec: float = 120.0


class ProviderCredentials(BaseModel):
    """Per-provider credential and endpoint overrides."""

    api_key: str = ""
    base_url: str | None = None
    default_model: str | None = None


class ProvidersSettings(BaseModel):
    """Map canonical provider id → credentials (declared in yaml / env)."""

    entries: dict[str, ProviderCredentials] = Field(default_factory=dict)


class ToolPermissionPolicy(BaseModel):
    """Process-wide caps for tool permission *classes* (independent of ``--approve-all``).

    ``--approve-all`` only skips ``approval_required`` prompts; these flags (or an
    interactive session grant) are required for NETWORK / WRITE / EXECUTE / EXTERNAL
    when set to False.
    """

    allow_network: bool = False
    allow_write: bool = False
    allow_execute: bool = False
    allow_external: bool = False


class ToolsSettings(BaseModel):
    """Tool-related configuration (search, eval aux, MCP placeholders)."""

    tavily_api_key: str = ""
    http_proxy: str = ""
    ollama_api_key: str = ""
    # A4 placeholders — keep keys so schema stays stable
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    permission_policy: ToolPermissionPolicy = Field(default_factory=ToolPermissionPolicy)


class WeixinChannelSettings(BaseModel):
    """Personal WeChat via Tencent ilink HTTP API (same stack as HKUDS nanobot).

    Defaults match nanobot: no need to set ``base_url`` unless you use a custom
    gateway. Authenticate with ``edu channels login weixin`` (token persisted under
    ``state_dir`` / ``account.json``).
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    base_url: str = "https://ilinkai.weixin.qq.com"
    cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c"
    token: str = ""
    state_dir: str = ""
    poll_timeout_sec: float = 35.0
    poll_interval_sec: float = 2.0
    allow_from: list[str] = Field(default_factory=list)
    route_tag: str = ""


class ChannelsSettings(BaseModel):
    """``runtime.channels`` in ``edu_agent.yaml``."""

    model_config = ConfigDict(extra="ignore")

    http_enabled: bool = True
    websocket_enabled: bool = True
    weixin: WeixinChannelSettings = Field(default_factory=WeixinChannelSettings)


class RuntimeSettings(BaseModel):
    """Process-level runtime flags (not session state)."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    default_timezone: str = "UTC"
    env: Literal["dev", "staging", "prod"] = "dev"
    # A5 placeholders
    gateway: dict[str, Any] = Field(default_factory=dict)
    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)


class EduSettings(BaseModel):
    """Root EduAgent configuration — loaded only via config_loader.load_settings()."""

    agent: AgentDefaults = Field(default_factory=AgentDefaults)
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    toolsets: ToolsetsSettings = Field(default_factory=ToolsetsSettings)
