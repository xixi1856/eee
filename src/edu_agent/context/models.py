"""Context / compression configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

_DEFAULT_TOOL_PRUNE_PLACEHOLDER = "[Old tool output cleared to save context space]"


class ContextConfig(BaseModel):
    """Token budgeting and compression (Hermes-inspired layered thresholds)."""

    token_limit_percent: float = Field(default=0.6, ge=0.05, le=1.0)
    pre_check_ratio: float = Field(
        default=0.85,
        ge=0.1,
        le=1.0,
        description="Rough-estimate pre-check threshold (vs model context), analogous to gateway hygiene.",
    )
    compression_ratio: float = Field(default=0.5, ge=0.1, le=1.0)
    idle_timeout_sec: int = Field(default=3600, ge=60)
    compression_enabled: bool = True
    summary_trigger_multiplier: float = Field(
        default=1.2,
        ge=1.0,
        description="Optional: only call LLM summarizer when estimated tokens exceed limit * this factor.",
    )
    summary_failure_cooldown_sec: float = Field(default=600.0, ge=0.0)
    protect_last_rounds: int = Field(default=3, ge=1, le=20)
    model_max_tokens: int = Field(default=4096, ge=256)
    # Hermes-style tail / head (Tier B)
    protect_first_n: int = Field(
        default=0,
        ge=0,
        le=50,
        description="First N messages stay verbatim (head); never summarized into middle.",
    )
    compression_target_ratio: float = Field(
        default=0.2,
        ge=0.05,
        le=0.9,
        description="Tail token budget as fraction of token_limit (Hermes target_ratio).",
    )
    protect_last_n_messages: int = Field(
        default=8,
        ge=1,
        le=100,
        description="Minimum recent messages included when building token-based tail.",
    )
    tool_prune_min_chars: int = Field(
        default=200,
        ge=0,
        description="Phase1: replace tool message bodies longer than this in middle (0 = off).",
    )
    tool_prune_placeholder: str = Field(
        default=_DEFAULT_TOOL_PRUNE_PLACEHOLDER,
        description="Replacement text for pruned tool outputs.",
    )
    gateway_hygiene_enabled: bool = Field(
        default=True,
        description="If true, run_turn may trigger early compaction at gateway_hygiene_ratio.",
    )
    gateway_hygiene_ratio: float = Field(
        default=0.85,
        ge=0.1,
        le=1.0,
        description="Rough token estimate / model_max threshold for gateway-style pre-compaction.",
    )
    max_tool_chains_pulled_into_tail: int | None = Field(
        default=None,
        description="Cap how many tool chains (from the tail side) expand tail_start; None = no cap.",
    )
