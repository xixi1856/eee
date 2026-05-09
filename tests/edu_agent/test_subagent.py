"""Tests for edu_agent/subagent.py and the delegate_task tool wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import edu_agent.subagent as subagent_mod
from edu_agent.config import EduSettings
from edu_agent.subagent import SubAgent, _MAX_CONCURRENT
from edu_agent.tools import execute_tool
from edu_agent.types import SubAgentConfig, SubTaskResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_choice(content=None, tool_calls=None, finish_reason="stop"):
    choice = MagicMock()
    choice.finish_reason = finish_reason
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice.message = msg
    return choice


def _make_response(choices):
    resp = MagicMock()
    resp.choices = choices
    return resp


def _make_sub_agent(
    reply: str | None = "子任务完成",
    *,
    settings: EduSettings,
) -> tuple[SubAgent, MagicMock]:
    """Return a SubAgent wired to a mock OpenAI client."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response(
        [_make_choice(content=reply, finish_reason="stop")]
    )
    agent = SubAgent(model="mock", client=mock_client, settings=settings)
    return agent, mock_client


# ---------------------------------------------------------------------------
# SubAgentConfig & SubTaskResult dataclasses
# ---------------------------------------------------------------------------


class TestSubAgentConfig:
    def test_defaults(self):
        cfg = SubAgentConfig(task="做一件事")
        assert cfg.allowed_tools == []
        assert cfg.max_iterations == 5
        assert cfg.model == ""

    def test_custom_values(self):
        cfg = SubAgentConfig(
            task="t",
            allowed_tools=["knowledge_query"],
            max_iterations=3,
        )
        assert cfg.allowed_tools == ["knowledge_query"]
        assert cfg.max_iterations == 3


class TestSubTaskResult:
    def test_success_result(self):
        r = SubTaskResult(success=True, summary="ok")
        assert r.success is True
        assert r.error == ""
        assert r.payload is None

    def test_failure_result(self):
        r = SubTaskResult(success=False, summary="", error="boom")
        assert r.success is False
        assert "boom" in r.error


# ---------------------------------------------------------------------------
# SubAgent.run() – happy path
# ---------------------------------------------------------------------------


class TestSubAgentRun:
    def test_no_tools_single_turn(self, minimal_edu_settings: EduSettings):
        agent, _ = _make_sub_agent("任务完成摘要", settings=minimal_edu_settings)
        cfg = SubAgentConfig(task="概括一下TCP", allowed_tools=[])
        result = agent.run(cfg)
        assert result.success is True
        assert result.summary == "任务完成摘要"
        assert result.iterations == 1

    def test_result_summary_matches_llm_reply(self, minimal_edu_settings: EduSettings):
        agent, _ = _make_sub_agent("这是最终结果", settings=minimal_edu_settings)
        result = agent.run(SubAgentConfig(task="t"))
        assert result.summary == "这是最终结果"

    def test_custom_system_prompt_passed_to_llm(self, minimal_edu_settings: EduSettings):
        agent, mock_client = _make_sub_agent("ok", settings=minimal_edu_settings)
        cfg = SubAgentConfig(task="t", system_prompt="自定义系统提示")
        agent.run(cfg)
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        system_msg = call_kwargs["messages"][0]
        assert system_msg["role"] == "system"
        assert "自定义系统提示" in system_msg["content"]

    def test_tool_call_then_final_answer(
        self, minimal_edu_settings: EduSettings, with_turn_runtime
    ):
        """Sub-agent handles one tool call round-trip."""
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall as TC,
            Function,
        )

        from edu_agent.types import ToolResult

        tc = TC(id="tc1", type="function", function=Function(name="knowledge_query", arguments='{"question":"q"}'))
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tc], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="工具调用后的最终结果", finish_reason="stop")]),
        ]
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)

        with patch(
            "edu_agent.toolsets.runtime.ToolRuntime.execute",
            new_callable=AsyncMock,
            return_value=(
                '{"result": "RAG 结果"}',
                ToolResult(tool_name="knowledge_query", success=True, summary="RAG 结果"),
            ),
        ):
            result = agent.run(
                SubAgentConfig(task="q", allowed_tools=["knowledge_query"])
            )

        assert result.success is True
        assert result.summary == "工具调用后的最终结果"
        assert mock_client.chat.completions.create.call_count == 2

    def test_llm_error_returns_failure(self, minimal_edu_settings: EduSettings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)
        result = agent.run(SubAgentConfig(task="t"))
        assert result.success is False
        assert "API down" in result.error


# ---------------------------------------------------------------------------
# Iteration budget exhaustion
# ---------------------------------------------------------------------------


class TestSubAgentBudget:
    def test_budget_exhaustion_returns_failure(self, minimal_edu_settings: EduSettings):
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall as TC,
            Function,
        )
        tc = TC(id="tc", type="function", function=Function(name="knowledge_query", arguments="{}"))
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(
            [_make_choice(tool_calls=[tc], finish_reason="tool_calls")]
        )
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)

        from edu_agent.types import ToolResult

        with patch(
            "edu_agent.toolsets.runtime.ToolRuntime.execute",
            new_callable=AsyncMock,
            return_value=(
                '{"result": "x"}',
                ToolResult(tool_name="knowledge_query", success=True, summary="x"),
            ),
        ):
            result = agent.run(
                SubAgentConfig(task="loop forever", allowed_tools=["knowledge_query"], max_iterations=2)
            )
        assert result.success is False
        assert "迭代预算" in result.error


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------


class TestRecursionGuard:
    def test_recursion_blocked_when_depth_already_active(self, minimal_edu_settings: EduSettings):
        agent, _ = _make_sub_agent("ok", settings=minimal_edu_settings)
        tok = subagent_mod._subagent_depth.set(1)
        try:
            result = agent.run(SubAgentConfig(task="nested"))
        finally:
            subagent_mod._subagent_depth.reset(tok)
        assert result.success is False
        assert "递归" in result.error

    def test_depth_cleared_after_successful_run(self, minimal_edu_settings: EduSettings):
        agent, _ = _make_sub_agent("ok", settings=minimal_edu_settings)
        agent.run(SubAgentConfig(task="t"))
        assert subagent_mod._subagent_depth.get() == 0

    def test_depth_cleared_after_llm_error(self, minimal_edu_settings: EduSettings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("err")
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)
        agent.run(SubAgentConfig(task="t"))
        assert subagent_mod._subagent_depth.get() == 0


# ---------------------------------------------------------------------------
# Tool whitelist enforcement
# ---------------------------------------------------------------------------


class TestToolWhitelist:
    def test_disallowed_tool_blocked(self, minimal_edu_settings: EduSettings):
        """A tool not in allowed_tools must not be called."""
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall as TC,
            Function,
        )
        tc = TC(id="tc", type="function", function=Function(name="score_essay", arguments="{}"))
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tc], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="fallback", finish_reason="stop")]),
        ]
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)

        with patch(
            "edu_agent.toolsets.runtime.ToolRuntime.execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            agent.run(SubAgentConfig(task="t", allowed_tools=["knowledge_query"]))
        for call in mock_exec.call_args_list:
            assert call.args[0] != "score_essay"

    def test_delegate_task_always_blocked(self, minimal_edu_settings: EduSettings):
        """delegate_task must be blocked even if explicitly listed in allowed_tools."""
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall as TC,
            Function,
        )
        tc = TC(id="tc", type="function", function=Function(name="delegate_task", arguments='{"task":"x"}'))
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tc], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="done", finish_reason="stop")]),
        ]
        agent = SubAgent(model="mock", client=mock_client, settings=minimal_edu_settings)

        with patch(
            "edu_agent.toolsets.runtime.ToolRuntime.execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            agent.run(SubAgentConfig(task="t", allowed_tools=["delegate_task"]))
        for call in mock_exec.call_args_list:
            assert call.args[0] != "delegate_task"


# ---------------------------------------------------------------------------
# delegate_task tool wrapper in tools.py
# ---------------------------------------------------------------------------


class TestSubAgentRuntimeContext:
    def test_subagent_sets_turn_runtime_with_parent_session_suffix(
        self, minimal_edu_settings, with_turn_runtime, mocker
    ):
        """SubAgent.run must push its own ContextVar so tools see sub purpose paths."""
        from edu_agent.runtime_context import set_current_runtime as set_turn_rt
        from edu_agent.subagent import SubAgent

        spy = mocker.patch("edu_agent.subagent.set_current_runtime", wraps=set_turn_rt)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="done", finish_reason="stop")]
        )
        agent = SubAgent(client=mock_client, settings=minimal_edu_settings)
        agent.run(SubAgentConfig(task="t"))
        spy.assert_called_once()
        ctx = spy.call_args[0][0]
        assert ctx.session_id.endswith(":sub")

    def test_subagent_inherits_parent_paths_not_settings_defaults(
        self, minimal_edu_settings, mocker
    ):
        """When nested under a turn, SubAgent must reuse parent.paths (session overrides)."""
        from edu_agent.paths import build_paths
        from edu_agent.providers.runtime import resolve_provider_runtime
        from edu_agent.runtime_context import (
            TurnRuntimeContext,
            reset_current_runtime,
            set_current_runtime,
        )
        from edu_agent.subagent import SubAgent

        root = minimal_edu_settings.agent.workspace
        override = root / "session_override"
        override.mkdir(parents=True, exist_ok=True)
        skills = override / "cli_skills"
        skills.mkdir()
        parent_paths = build_paths(
            minimal_edu_settings,
            workspace=str(override),
            skills_dir=str(skills),
        )
        rt = resolve_provider_runtime(minimal_edu_settings, None, "main")
        parent_ctx = TurnRuntimeContext(
            settings=minimal_edu_settings,
            paths=parent_paths,
            provider_runtime=rt,
            user_id="u",
            session_id="parent",
        )
        tok = set_current_runtime(parent_ctx)
        try:
            wrong_paths = build_paths(minimal_edu_settings)
            assert wrong_paths.skills_dir != parent_paths.skills_dir

            spy = mocker.patch("edu_agent.subagent.set_current_runtime", wraps=set_current_runtime)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_response(
                [_make_choice(content="ok", finish_reason="stop")]
            )
            SubAgent(client=mock_client, settings=minimal_edu_settings).run(
                SubAgentConfig(task="t")
            )
            sub_ctx = spy.call_args[0][0]
            assert sub_ctx.paths == parent_paths
            assert sub_ctx.paths.skills_dir == parent_paths.skills_dir
        finally:
            reset_current_runtime(tok)


class TestDelegateTaskTool:
    def test_success_returns_tool_result(self, with_turn_runtime):
        with patch("edu_agent.tools.delegation.SubAgent") as MockSA:
            inst = MagicMock()
            inst.arun = AsyncMock(
                return_value=SubTaskResult(success=True, summary="子任务摘要")
            )
            MockSA.return_value = inst
            result = execute_tool("delegate_task", {"task": "出三道题"})
        assert result.success is True
        assert result.summary == "子任务摘要"

    def test_failure_passes_through_error(self, with_turn_runtime):
        with patch("edu_agent.tools.delegation.SubAgent") as MockSA:
            inst = MagicMock()
            inst.arun = AsyncMock(
                return_value=SubTaskResult(success=False, summary="", error="迭代预算超限")
            )
            MockSA.return_value = inst
            result = execute_tool("delegate_task", {"task": "t"})
        assert result.success is False
        assert "迭代预算超限" in result.error

    def test_max_iterations_clamped(self, with_turn_runtime):
        """max_iterations=99 should be silently clamped to 10."""
        captured: dict = {}

        async def fake_arun(cfg: SubAgentConfig) -> SubTaskResult:
            captured["max_iter"] = cfg.max_iterations
            return SubTaskResult(success=True, summary="ok")

        with patch("edu_agent.tools.delegation.SubAgent") as MockSA:
            inst = MagicMock()
            inst.arun = AsyncMock(side_effect=fake_arun)
            MockSA.return_value = inst
            execute_tool("delegate_task", {"task": "t", "max_iterations": 99})
        assert captured["max_iter"] == 10
