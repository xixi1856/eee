"""Per-turn runtime injected via ContextVar (no global settings singleton)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional

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
    memory_enabled: bool = False
    memory_store: Any | None = None
    memory_retriever: Any | None = None
    # A4: shared ToolRuntime for main agent and SubAgent (optional in tests)
    tool_runtime: Any | None = None
    # Same PermissionChecker as main agent (SubAgent must not mint a looser checker).
    permission_checker: Any | None = None
    # Optional course scope for knowledge_query routing (phase4).
    course_id: str | None = None
    # Optional lesson scope (B3 platform runtime; not passed to knowledge_query args).
    lesson_id: str | None = None


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
