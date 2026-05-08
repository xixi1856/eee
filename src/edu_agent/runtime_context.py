"""Per-turn runtime injected via ContextVar (no global settings singleton)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

from pydantic import BaseModel, ConfigDict

from edu_agent.config import EduSettings
from edu_agent.paths import EduPaths
from edu_agent.providers.types import ResolvedProviderRuntime

_ctx: ContextVar[Optional["TurnRuntimeContext"]] = ContextVar("edu_agent_turn_runtime", default=None)


class TurnRuntimeContext(BaseModel):
    """Everything tool handlers may read for the current agent turn."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: EduSettings
    paths: EduPaths
    provider_runtime: ResolvedProviderRuntime
    user_id: str
    session_id: str


def set_current_runtime(ctx: TurnRuntimeContext) -> Token[Optional[TurnRuntimeContext]]:
    return _ctx.set(ctx)


def reset_current_runtime(token: Token[Optional[TurnRuntimeContext]]) -> None:
    _ctx.reset(token)


def get_current_runtime() -> TurnRuntimeContext:
    """Return the active turn runtime or raise if outside a turn."""
    cur = _ctx.get()
    if cur is None:
        msg = "No active EduAgent runtime context; tools must run inside EduAgent.run_turn()."
        raise RuntimeError(msg)
    return cur
