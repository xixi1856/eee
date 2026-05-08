"""Core data types for the educational agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # forward-reference guard


@dataclass
class ToolResult:
    """Standardised result returned by every tool handler."""

    tool_name: str
    success: bool
    # Short human-readable description of the outcome (always set).
    summary: str
    # Optional structured payload for downstream processing.
    payload: Any = None
    # Error description when success=False.
    error: str = ""

    def to_content(self) -> str:
        """Serialise to the string content expected by the tool-role message."""
        if self.success:
            return self.summary
        return f"[工具调用失败: {self.tool_name}] {self.error}"


@dataclass
class AgentCallbacks:
    """Optional event hooks wired from CLI → Agent for real-time feedback.

    All fields are optional callables; set to None to skip.  The agent
    calls each hook via ``_safe_cb()`` so exceptions never propagate back.

    Hook signatures
    ---------------
    on_thinking_start()                              — before every LLM API call
    on_thinking_end()                                — when LLM response type is known
                                                       (first text delta or tool_calls end)
    on_tool_start(tool_name, args)                   — before tool execution
    on_tool_end(tool_name, args, result, duration_s) — after tool execution
    on_text_chunk(chunk)                             — each streaming text delta;
                                                       if set, streaming is enabled
    """

    on_thinking_start: Callable[[], None] | None = None
    on_thinking_end: Callable[[], None] | None = None
    on_tool_start: Callable[[str, dict], None] | None = None
    on_tool_end: Callable[[str, dict, Any, float], None] | None = None
    on_text_chunk: Callable[[str], None] | None = None
    # Returns True if at least one text chunk was emitted this turn.
    # Set by build_callbacks(); CLI uses it to avoid double-printing.
    was_streamed: Callable[[], bool] | None = None


@dataclass
class AgentConfig:
    """Session-scoped overrides for EduAgent (not global settings).

    Paths are derived from ``EduSettings`` + ``EduPaths``; only optional overrides
    live here. Empty string means “use root settings / paths default”.
    """

    user_id: str = "default"
    session_id: str = ""
    model: str = ""
    provider: str = ""
    workspace: str = ""
    skills_dir: str = ""
    max_iterations: int = 20


@dataclass
class SubAgentConfig:
    """Configuration for a SubAgent delegation call.

    Attributes:
        task:            Natural-language description of the sub-task.
        allowed_tools:   Whitelist of tool names the sub-agent may call.
                         Empty list means *no tools allowed* (pure LLM).
        max_iterations:  Hard cap on LLM+tool iterations (default 5).
        model:           Override model; falls back to caller's model when "".
        system_prompt:   Override system prompt; empty string uses a minimal
                         default focused on task completion.
    """

    task: str
    allowed_tools: list[str] = field(default_factory=list)
    max_iterations: int = 5
    model: str = ""
    system_prompt: str = ""


@dataclass
class SubTaskResult:
    """Result returned by SubAgent.run().

    Attributes:
        success:    Whether the sub-agent completed the task without errors.
        summary:    Human-readable summary of what was accomplished.
        payload:    Optional structured data (e.g. quiz questions, extracted text).
        error:      Error description when success=False.
        iterations: Number of LLM iterations consumed.
    """

    success: bool
    summary: str
    payload: Any = None
    error: str = ""
    iterations: int = 0
