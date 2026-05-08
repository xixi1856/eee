"""SubAgent: isolated sub-task delegation with tool whitelist and recursion guard.

Design principles (from the execution plan Phase 4):
- Isolated context: SubAgent does NOT inherit the caller's message history.
- Tool whitelist: only tools listed in SubAgentConfig.allowed_tools may be called.
- No recursion: SubAgent cannot itself call delegate_task.
- Result summary passback: caller receives SubTaskResult (summary + optional payload).
- Thread-safe concurrency limit: module-level semaphore caps parallel sub-agents.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall

from edu_agent.config import EduSettings
from edu_agent.paths import build_paths
from edu_agent.providers.runtime import build_openai_client, resolve_provider_runtime
from edu_agent.registry import registry as _registry
from edu_agent.runtime_context import (
    TurnRuntimeContext,
    get_current_runtime,
    reset_current_runtime,
    set_current_runtime,
)
from edu_agent.tools import TOOL_SCHEMAS
from edu_agent.types import AgentConfig, SubAgentConfig, SubTaskResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global concurrency limit – prevents runaway parallel delegation.
# ---------------------------------------------------------------------------

_MAX_CONCURRENT: int = 4
_semaphore = threading.Semaphore(_MAX_CONCURRENT)

# ---------------------------------------------------------------------------
# Recursion guard – per-thread flag set when a SubAgent is running.
# ---------------------------------------------------------------------------

_subagent_active = threading.local()

_MINIMAL_SYSTEM = (
    "你是一个专注的任务执行助手。"
    "请严格完成以下子任务，不要闲聊，输出简洁结构化的结果。"
)

# delegate_task is blacklisted so SubAgents cannot recurse.
_RECURSION_BLACKLIST = {"delegate_task"}


def _filter_schemas(allowed_tools: list[str]) -> list[dict]:
    """Return the subset of TOOL_SCHEMAS whose names are in *allowed_tools*
    and are not on the recursion blacklist."""
    permitted = set(allowed_tools) - _RECURSION_BLACKLIST
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] in permitted]


class SubAgent:
    """Isolated, single-use sub-agent for delegated sub-tasks.

    Usage::

        cfg = SubAgentConfig(task="生成三道TCP相关练习题", allowed_tools=["generate_quiz"])
        result = SubAgent(model="qwen-plus-2025-04-28").run(cfg)

    The sub-agent runs synchronously in the caller's thread but respects the
    module-level concurrency semaphore so tests and production code can cap
    parallel execution.
    """

    def __init__(
        self,
        model: str = "",
        client: OpenAI | None = None,
        settings: EduSettings | None = None,
    ) -> None:
        """Create a SubAgent.

        Args:
            model: Default LLM model when ``SubAgentConfig.model`` is empty.
            client: Pre-built OpenAI client (tests inject mocks).
            settings: Root settings; when omitted, ``run()`` reads from
                ``get_current_runtime()`` (e.g. inside ``delegate_task``).
        """
        self._settings = settings
        self._default_model = model
        self._injected_client = client
        if client is not None:
            self._client = client
            self._model = model or "mock-model"
            self._temperature = 0.7
            self._max_tokens = 2048
        else:
            self._client = None  # resolved in ``run()`` from settings + registry
            self._model = ""
            self._temperature = 0.7
            self._max_tokens = 2048

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, config: SubAgentConfig) -> SubTaskResult:
        """Execute *config.task* in an isolated context.

        Returns:
            SubTaskResult with success flag, summary, optional payload,
            and number of iterations consumed.

        Raises:
            Nothing – all exceptions are caught and returned as failure results.
        """
        # Recursion guard
        if getattr(_subagent_active, "active", False):
            return SubTaskResult(
                success=False,
                summary="",
                error="禁止递归委派：SubAgent 内部不可再次调用 delegate_task。",
            )

        # Concurrency cap
        if not _semaphore.acquire(blocking=False):
            return SubTaskResult(
                success=False,
                summary="",
                error=f"子任务并发上限 ({_MAX_CONCURRENT}) 已达，请稍后重试。",
            )
        try:
            _subagent_active.active = True
            return self._run_with_runtime(config)
        finally:
            _subagent_active.active = False
            _semaphore.release()

    def _run_with_runtime(self, config: SubAgentConfig) -> SubTaskResult:
        settings = self._settings
        if settings is None:
            try:
                settings = get_current_runtime().settings
            except RuntimeError:
                settings = None

        model_hint = (config.model or self._default_model or "").strip()
        overrides = AgentConfig(model=model_hint) if model_hint else None

        rt = resolve_provider_runtime(settings, overrides, "subagent") if settings is not None else None

        if self._injected_client is None:
            if settings is None or rt is None:
                return SubTaskResult(
                    success=False,
                    summary="",
                    error=(
                        "SubAgent 缺少 EduSettings：请在入口传入 settings，"
                        "或仅在已激活主 Agent runtime 的上下文内运行。"
                    ),
                    iterations=0,
                )
            self._client = build_openai_client(rt)
            self._model = rt.model
            self._temperature = rt.temperature
            self._max_tokens = rt.max_tokens

        if settings is not None and rt is not None:
            # Prefer parent turn paths so session-level workspace/skills_dir overrides
            # (AgentConfig → EduAgent._paths) are visible to SubAgent tools; otherwise
            # build_paths(settings) would drop those overrides and diverge from the main agent.
            try:
                parent = get_current_runtime()
                paths = parent.paths
                uid, sid = parent.user_id, f"{parent.session_id}:sub"
            except RuntimeError:
                paths = build_paths(settings)
                uid, sid = "subagent", "isolated"
            sub_tok = set_current_runtime(
                TurnRuntimeContext(
                    settings=settings,
                    paths=paths,
                    provider_runtime=rt,
                    user_id=uid,
                    session_id=sid,
                )
            )
            try:
                return self._run_isolated(config)
            finally:
                reset_current_runtime(sub_tok)

        return self._run_isolated(config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_isolated(self, config: SubAgentConfig) -> SubTaskResult:
        """Core ReAct loop running in an isolated message context."""
        system = config.system_prompt.strip() or _MINIMAL_SYSTEM
        tool_schemas = _filter_schemas(config.allowed_tools)

        # Isolated message history – does NOT inherit parent messages.
        messages: list[dict] = [{"role": "user", "content": config.task}]

        iteration = 0
        last_payload: Any = None

        for iteration in range(1, config.max_iterations + 1):
            logger.debug("SubAgent iteration %d/%d", iteration, config.max_iterations)

            create_kwargs: dict[str, Any] = dict(
                model=self._model,
                messages=cast(
                    list[Any],
                    [{"role": "system", "content": system}] + messages,
                ),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if tool_schemas:
                create_kwargs["tools"] = cast(list[Any], tool_schemas)

            try:
                response = self._client.chat.completions.create(**create_kwargs)
            except Exception as exc:
                logger.error("SubAgent LLM call failed: %s", exc)
                return SubTaskResult(
                    success=False,
                    summary="",
                    error=str(exc),
                    iterations=iteration,
                )

            choice = response.choices[0]
            finish_reason: str | None = choice.finish_reason
            msg: ChatCompletionMessage = choice.message

            if finish_reason == "tool_calls" or msg.tool_calls:
                tool_payload = self._handle_tool_calls(msg, messages, config.allowed_tools)
                if tool_payload is not None:
                    last_payload = tool_payload
                continue

            # Final answer
            content = msg.content or ""
            messages.append({"role": "assistant", "content": content})
            logger.debug("SubAgent finished in %d iteration(s)", iteration)
            return SubTaskResult(
                success=True,
                summary=content,
                payload=last_payload,
                iterations=iteration,
            )

        # Budget exhausted
        return SubTaskResult(
            success=False,
            summary="",
            error=f"子任务超出迭代预算（{config.max_iterations} 轮）。",
            iterations=iteration,
        )

    def _handle_tool_calls(
        self,
        msg: ChatCompletionMessage,
        messages: list[dict],
        allowed_tools: list[str],
    ) -> Any:
        """Execute tools, append results to *messages*, return last payload."""
        messages.append(msg.model_dump(exclude_unset=True))
        last_payload: Any = None

        for tc in msg.tool_calls or []:
            if not isinstance(tc, ChatCompletionMessageToolCall):
                continue
            tool_name = tc.function.name

            # Whitelist enforcement (belt-and-suspenders)
            if tool_name not in allowed_tools or tool_name in _RECURSION_BLACKLIST:
                logger.warning("SubAgent blocked disallowed tool: %s", tool_name)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[工具已禁用: {tool_name}]",
                    }
                )
                continue

            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            logger.info("SubAgent calling tool: %s(%s)", tool_name, args)
            result_content = _registry.dispatch(tool_name, args)
            logger.info(
                "SubAgent tool %s → content_len=%d",
                tool_name,
                len(result_content),
            )
            try:
                parsed = json.loads(result_content)
                if isinstance(parsed, dict) and parsed.get("payload") is not None:
                    last_payload = parsed.get("payload")
            except json.JSONDecodeError:
                pass

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_content,
                }
            )

        return last_payload
