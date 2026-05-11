"""SubAgent: isolated sub-task delegation with tool whitelist and recursion guard (A4 async)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall

from edu_agent.config import EduSettings
from edu_agent.llm_tools import tool_specs_to_openai_tools
from edu_agent.paths import build_paths
from edu_agent.providers.runtime import build_openai_client, resolve_provider_runtime
from edu_agent.runtime_context import (
    TurnRuntimeContext,
    get_current_runtime,
    reset_current_runtime,
    set_current_runtime,
)
from edu_agent.toolsets import PermissionChecker, ToolRuntime
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.types import AgentConfig, SubAgentConfig, SubTaskResult

logger = logging.getLogger(__name__)

_MAX_CONCURRENT: int = 4
_async_sem: asyncio.Semaphore | None = None

_subagent_depth: ContextVar[int] = ContextVar("subagent_depth", default=0)

_MINIMAL_SYSTEM = (
    "你是一个专注的任务执行助手。"
    "请严格完成以下子任务，不要闲聊，输出简洁结构化的结果。"
)

_RECURSION_BLACKLIST = {"delegate_task"}


def _get_async_sem() -> asyncio.Semaphore:
    global _async_sem
    if _async_sem is None:
        _async_sem = asyncio.Semaphore(_MAX_CONCURRENT)
    return _async_sem


def _schemas_for_allowed(settings: EduSettings, allowed_tools: list[str]) -> list[dict]:
    permitted = frozenset(allowed_tools) - _RECURSION_BLACKLIST
    specs = [s for s in toolset_registry.list_specs(settings) if s.name in permitted]
    return tool_specs_to_openai_tools(specs)


class SubAgent:
    """Isolated sub-agent; use ``await arun(cfg)`` from async code."""

    def __init__(
        self,
        model: str = "",
        client: OpenAI | None = None,
        settings: EduSettings | None = None,
    ) -> None:
        self._settings = settings
        self._default_model = model
        self._injected_client = client
        if client is not None:
            self._client = client
            self._model = model or "mock-model"
            self._temperature = 0.7
            self._max_tokens = 2048
        else:
            self._client = None
            self._model = ""
            self._temperature = 0.7
            self._max_tokens = 2048

    def run(self, config: SubAgentConfig) -> SubTaskResult:
        """Sync entry for tests / REPL without a running loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(config))
        raise RuntimeError("Use await SubAgent.arun() when inside an async context")

    async def arun(self, config: SubAgentConfig) -> SubTaskResult:
        if _subagent_depth.get() > 0:
            return SubTaskResult(
                success=False,
                summary="",
                error="禁止递归委派：SubAgent 内部不可再次调用 delegate_task。",
            )
        async with _get_async_sem():
            depth_tok = _subagent_depth.set(1)
            try:
                return await self._run_with_runtime_async(config)
            finally:
                _subagent_depth.reset(depth_tok)

    async def _run_with_runtime_async(
        self,
        config: SubAgentConfig,
    ) -> SubTaskResult:
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
            parent_ctx: TurnRuntimeContext | None = None
            try:
                parent_ctx = get_current_runtime()
                paths = parent_ctx.paths
                uid, sid = parent_ctx.user_id, f"{parent_ctx.session_id}:sub"
                tool_rt = parent_ctx.tool_runtime
                parent_checker = parent_ctx.permission_checker
            except RuntimeError:
                paths = build_paths(settings)
                uid, sid = "subagent", "isolated"
                tool_rt = None
                parent_checker = None
            if tool_rt is None:
                from edu_agent.config import ToolPermissionPolicy

                isolated_policy = settings.tools.permission_policy
                if not isinstance(isolated_policy, ToolPermissionPolicy):
                    isolated_policy = ToolPermissionPolicy()
                checker = PermissionChecker(
                    isolated_policy,
                    approve_all=False,
                    interactive=False,
                )
                tool_rt = ToolRuntime(
                    toolset_registry,
                    settings,
                    checker,
                )
            else:
                checker = parent_checker or tool_rt.permission_checker
            sub_course = parent_ctx.course_id if parent_ctx is not None else None
            sub_lesson = parent_ctx.lesson_id if parent_ctx is not None else None
            sub_tok = set_current_runtime(
                TurnRuntimeContext(
                    settings=settings,
                    paths=paths,
                    provider_runtime=rt,
                    user_id=uid,
                    session_id=sid,
                    tool_runtime=tool_rt,
                    permission_checker=checker,
                    course_id=sub_course,
                    lesson_id=sub_lesson,
                )
            )
            try:
                return await self._run_isolated_async(config, settings)
            finally:
                reset_current_runtime(sub_tok)

        return await self._run_isolated_async(config, settings)

    async def _run_isolated_async(
        self,
        config: SubAgentConfig,
        settings: EduSettings | None,
    ) -> SubTaskResult:
        system = config.system_prompt.strip() or _MINIMAL_SYSTEM
        if settings is None:
            return SubTaskResult(success=False, summary="", error="缺少 settings", iterations=0)
        tool_schemas = _schemas_for_allowed(settings, config.allowed_tools)
        permitted = frozenset(config.allowed_tools) - _RECURSION_BLACKLIST

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
                tool_payload = await self._handle_tool_calls(msg, messages, permitted)
                if tool_payload is not None:
                    last_payload = tool_payload
                continue

            content = msg.content or ""
            messages.append({"role": "assistant", "content": content})
            logger.debug("SubAgent finished in %d iteration(s)", iteration)
            return SubTaskResult(
                success=True,
                summary=content,
                payload=last_payload,
                iterations=iteration,
            )

        return SubTaskResult(
            success=False,
            summary="",
            error=f"子任务超出迭代预算（{config.max_iterations} 轮）。",
            iterations=iteration,
        )

    async def _handle_tool_calls(
        self,
        msg: ChatCompletionMessage,
        messages: list[dict],
        permitted: frozenset[str],
    ) -> Any:
        messages.append(msg.model_dump(exclude_unset=True))
        last_payload: Any = None
        ctx = get_current_runtime()
        trt = ctx.tool_runtime
        if trt is None:
            for tc in msg.tool_calls or []:
                if not isinstance(tc, ChatCompletionMessageToolCall):
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": "ToolRuntime 未配置"}, ensure_ascii=False),
                    }
                )
            return None

        for tc in msg.tool_calls or []:
            if not isinstance(tc, ChatCompletionMessageToolCall):
                continue
            tool_name = tc.function.name
            if tool_name not in permitted or tool_name in _RECURSION_BLACKLIST:
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
            result_content, _ = await trt.execute(
                tool_name,
                args,
                ctx,
                allowed_names=permitted,
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
