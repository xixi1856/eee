"""Tests for EduAgent.run_turn() ReAct loop (agent.py).

All LLM and tool calls are mocked so tests run without network access.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from edu_agent.agent import EduAgent
from edu_agent.config import EduSettings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.paths import build_paths
from edu_agent.sessions.store import SessionStore
from edu_agent.types import AgentConfig, ToolResult


def _run_turn(agent: EduAgent, text: str) -> str:
    """Sync wrapper — ``EduAgent.run_turn`` is async."""
    return asyncio.run(agent.run_turn(text))


# ---------------------------------------------------------------------------
# Helpers to build mock OpenAI chat completion objects
# ---------------------------------------------------------------------------

def _make_choice(content: str | None = None, tool_calls=None, finish_reason: str = "stop"):
    """Build a minimal mock matching openai.types.chat.ChatCompletion."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message = msg
    return choice


def _make_response(choices, *, prompt_tokens: int = 80, completion_tokens: int = 2):
    resp = MagicMock()
    resp.choices = choices
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    resp.usage = usage
    return resp


def _make_tool_call(call_id: str, name: str, arguments: str):
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall as _TC,
        Function,
    )
    return _TC(id=call_id, type="function", function=Function(name=name, arguments=arguments))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent(tmp_path, minimal_edu_settings: EduSettings):
    """EduAgent with minimal config, SQLite session store, and mocked OpenAI client."""
    skills_dir = tmp_path / "extra_skills"
    skills_dir.mkdir()
    config = AgentConfig(
        user_id="test_user",
        workspace=str(minimal_edu_settings.agent.workspace),
        skills_dir=str(skills_dir),
        memory_enabled=False,
    )

    paths = build_paths(
        minimal_edu_settings,
        workspace=config.workspace or None,
        skills_dir=config.skills_dir or None,
    )
    store = SessionStore(paths.sessions_db)

    with patch("edu_agent.providers.runtime.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        a = EduAgent(config, settings=minimal_edu_settings, session_store=store)
        a._client = mock_client  # make it accessible in tests
    yield a
    store.close()


# ---------------------------------------------------------------------------
# Single-turn tests (no tool calls)
# ---------------------------------------------------------------------------

class TestRuntimeContextLifecycle:
    def test_runtime_context_cleared_after_run_turn(self, agent):
        from edu_agent.runtime_context import get_current_runtime

        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="done", finish_reason="stop")]
        )
        _run_turn(agent, "hello")
        with pytest.raises(RuntimeError, match="No active"):
            get_current_runtime()


class TestRunTurnNoTools:
    def test_returns_assistant_reply(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="你好！我是教学助手。", finish_reason="stop")]
        )

        reply = _run_turn(agent, "你好")
        assert reply == "你好！我是教学助手。"

    def test_user_message_appended(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="回复", finish_reason="stop")]
        )

        _run_turn(agent, "用户消息")
        assert agent.messages[0] == {"role": "user", "content": "用户消息"}

    def test_assistant_reply_appended(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="助手回复", finish_reason="stop")]
        )

        _run_turn(agent, "问题")
        assert agent.messages[-1] == {"role": "assistant", "content": "助手回复"}

    def test_history_grows_across_turns(self, agent):
        for i in range(3):
            agent._client.chat.completions.create.return_value = _make_response(
                [_make_choice(content=f"回复{i}", finish_reason="stop")]
            )
            _run_turn(agent, f"问题{i}")

        # 3 user + 3 assistant = 6 messages
        assert len(agent.messages) == 6

    def test_system_prompt_passed_as_first_message(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="ok")]
        )

        _run_turn(agent, "hi")
        call_args = agent._client.chat.completions.create.call_args
        messages_arg = call_args[1]["messages"]
        assert messages_arg[0]["role"] == "system"

    def test_none_content_becomes_empty_string(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content=None, finish_reason="stop")]
        )
        reply = _run_turn(agent, "test")
        assert reply == ""


# ---------------------------------------------------------------------------
# Tool-call tests
# ---------------------------------------------------------------------------

class TestRunTurnWithToolCalls:
    def test_single_tool_call_then_final_answer(self, agent):
        """LLM calls a tool once, then returns a final answer."""
        tool_call = _make_tool_call("call_1", "knowledge_query", '{"question": "什么是TCP？"}')

        first_response = _make_response(
            [_make_choice(content=None, tool_calls=[tool_call], finish_reason="tool_calls")]
        )
        second_response = _make_response(
            [_make_choice(content="TCP 是传输控制协议。", finish_reason="stop")]
        )

        agent._client.chat.completions.create.side_effect = [first_response, second_response]

        with patch.object(
            agent._tool_runtime,
            "execute",
            new_callable=AsyncMock,
            return_value=(
                '{"result": "TCP 相关段落"}',
                ToolResult(tool_name="knowledge_query", success=True, summary="TCP 相关段落"),
            ),
        ):
            reply = _run_turn(agent, "什么是TCP？")

        assert reply == "TCP 是传输控制协议。"
        assert agent._client.chat.completions.create.call_count == 2

    def test_tool_result_appended_as_tool_role(self, agent):
        """After tool execution, a 'tool' role message is in messages."""
        tool_call = _make_tool_call("cid", "generate_quiz", "{}")

        agent._client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tool_call], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="题目已生成", finish_reason="stop")]),
        ]

        with patch.object(
            agent._tool_runtime,
            "execute",
            new_callable=AsyncMock,
            return_value=(
                '{"result": "题目内容"}',
                ToolResult(tool_name="generate_quiz", success=True, summary="题目内容"),
            ),
        ):
            _run_turn(agent, "出题")

        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "题目内容" in tool_messages[0]["content"]

    def test_failed_tool_result_content_includes_error(self, agent):
        tool_call = _make_tool_call("cid2", "knowledge_query", '{"question": "test"}')

        agent._client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tool_call], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="抱歉无法回答", finish_reason="stop")]),
        ]

        with patch.object(
            agent._tool_runtime,
            "execute",
            new_callable=AsyncMock,
            return_value=(
                '{"error": "DB error"}',
                ToolResult(
                    tool_name="knowledge_query",
                    success=False,
                    summary="",
                    error="DB error",
                ),
            ),
        ):
            _run_turn(agent, "test")

        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert "DB error" in tool_messages[0]["content"]

    def test_invalid_json_in_tool_args_does_not_crash(self, agent):
        """Malformed JSON arguments from LLM should not propagate as an exception."""
        tool_call = _make_tool_call("cid3", "knowledge_query", "INVALID JSON {{")

        agent._client.chat.completions.create.side_effect = [
            _make_response([_make_choice(tool_calls=[tool_call], finish_reason="tool_calls")]),
            _make_response([_make_choice(content="fallback", finish_reason="stop")]),
        ]

        # No tool mock needed — dispatch returns error JSON naturally with empty args
        reply = _run_turn(agent, "test")

        # Even with bad JSON, the agent should recover and return the final answer
        assert reply == "fallback"


# ---------------------------------------------------------------------------
# Iteration budget exhaustion
# ---------------------------------------------------------------------------

class TestIterationBudget:
    def test_budget_exhaustion_returns_message(self, agent):
        """If the LLM keeps requesting tools, budget caps the loop."""
        agent.config.max_iterations = 3

        # Always return tool_calls, never a final answer
        tool_call = _make_tool_call("cid", "generate_quiz", "{}")
        infinite_response = _make_response(
            [_make_choice(tool_calls=[tool_call], finish_reason="tool_calls")]
        )
        agent._client.chat.completions.create.return_value = infinite_response

        with patch.object(agent._tool_runtime, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = (
                '{"result": "x"}',
                ToolResult(tool_name="generate_quiz", success=True, summary="x"),
            )
            reply = _run_turn(agent, "一直出题")

        assert "最大" in reply or "推理" in reply
        assert agent._client.chat.completions.create.call_count == 3


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_messages(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="answer")]
        )
        _run_turn(agent, "hi")
        assert len(agent.messages) > 0

        agent.reset()
        assert agent.messages == []

    def test_reset_allows_fresh_conversation(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="a1")]
        )
        _run_turn(agent, "first turn")
        agent.reset()

        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="a2")]
        )
        _run_turn(agent, "fresh start")
        # Only 2 messages: 1 user + 1 assistant from the second turn
        assert len(agent.messages) == 2


# ---------------------------------------------------------------------------
# Safety integration tests
# ---------------------------------------------------------------------------

class TestSafetyIntegration:
    def test_harmful_input_blocked_without_calling_llm(self, agent):
        """A harmful user input must be rejected before the LLM is called."""
        with patch("edu_agent.agent.check_input") as mock_check:
            from edu_agent.safety import SafetyCheckResult
            mock_check.return_value = SafetyCheckResult(
                safe=False, reason="violence", categories=["violence"]
            )
            reply = _run_turn(agent, "如何杀死一个人")

        agent._client.chat.completions.create.assert_not_called()
        assert "抱歉" in reply or "不适当" in reply

    def test_safe_input_passes_through(self, agent):
        """A safe input must reach the LLM normally."""
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="TCP 是传输控制协议。")]
        )
        reply = _run_turn(agent, "什么是TCP？")
        assert reply == "TCP 是传输控制协议。"
        agent._client.chat.completions.create.assert_called_once()

    def test_harmful_output_replaced(self, agent):
        """If the LLM generates harmful output it must be replaced."""
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="危险内容")]
        )
        with patch("edu_agent.agent.check_output") as mock_out:
            from edu_agent.safety import SafetyCheckResult
            mock_out.return_value = SafetyCheckResult(
                safe=False, reason="test", categories=["violence"]
            )
            reply = _run_turn(agent, "普通问题")

        assert "无法提供" in reply or "抱歉" in reply
        assert reply != "危险内容"

    def test_blocked_input_still_persisted_to_session(self, agent):
        """Even blocked turns should be written to the session transcript."""
        with patch("edu_agent.agent.check_input") as mock_check:
            from edu_agent.safety import SafetyCheckResult
            mock_check.return_value = SafetyCheckResult(
                safe=False, reason="test", categories=["illegal"]
            )
            _run_turn(agent, "违规内容")

        rows = agent._session_store.list_messages(agent.config.session_id, limit=100)
        assert len(rows) == 2  # user turn + assistant (block message)


# ---------------------------------------------------------------------------
# Session persistence tests
# ---------------------------------------------------------------------------

class TestSessionResume:
    def test_resume_loads_prior_messages(self, tmp_path, minimal_edu_settings: EduSettings):
        """A second agent with the same session_id must reload OpenAI-shaped history."""
        skills_dir = tmp_path / "sk"
        skills_dir.mkdir()
        config = AgentConfig(
            user_id="resume_u",
            workspace=str(minimal_edu_settings.agent.workspace),
            skills_dir=str(skills_dir),
            memory_enabled=False,
        )
        paths = build_paths(minimal_edu_settings, workspace=config.workspace, skills_dir=config.skills_dir)
        store = SessionStore(paths.sessions_db)
        with patch("edu_agent.providers.runtime.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = _make_response(
                [_make_choice(content="first reply", finish_reason="stop")]
            )
            a1 = EduAgent(config, settings=minimal_edu_settings, session_store=store)
            sid = a1.config.session_id
            _run_turn(a1, "hello resume")
            mock_client.chat.completions.create.return_value = _make_response(
                [_make_choice(content="second", finish_reason="stop")]
            )
            a2 = EduAgent(
                AgentConfig(
                    user_id="resume_u",
                    workspace=str(minimal_edu_settings.agent.workspace),
                    skills_dir=str(skills_dir),
                    session_id=sid,
                    memory_enabled=False,
                ),
                settings=minimal_edu_settings,
                session_store=store,
            )
            a2._client = mock_client
            assert len(a2.messages) >= 2
            assert a2.messages[0]["role"] == "user"
            assert a2.messages[0]["content"] == "hello resume"
            assert any(m.get("role") == "assistant" for m in a2.messages)
            asst = next(m for m in a2.messages if m.get("role") == "assistant")
            assert asst.get("content") == "first reply"
        store.close()


class TestSessionPersistence:
    def test_turn_persisted_to_sqlite(self, agent):
        """Each run_turn call must persist user+assistant rows."""
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="回复")]
        )
        _run_turn(agent, "问题")

        rows = agent._session_store.list_messages(agent.config.session_id, limit=100)
        assert len(rows) == 2
        assert rows[0].metadata.role == "user"
        assert rows[1].metadata.role == "assistant"

    def test_multiple_turns_append(self, agent):
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="ok")]
        )
        _run_turn(agent, "turn 1")
        _run_turn(agent, "turn 2")

        rows = agent._session_store.list_messages(agent.config.session_id, limit=100)
        assert len(rows) == 4  # 2 turns × 2 roles


# ---------------------------------------------------------------------------
# Learner profile injection tests
# ---------------------------------------------------------------------------

class TestLearnerProfileInjection:
    def test_profile_summary_in_system_prompt(self, agent, tmp_path):
        """The learner profile summary should appear in the messages sent to LLM."""
        agent._profile_summary = "掌握较好的知识点：TCP。"
        agent._client.chat.completions.create.return_value = _make_response(
            [_make_choice(content="ok")]
        )
        _run_turn(agent, "问题")

        call_args = agent._client.chat.completions.create.call_args
        messages_sent = call_args[1]["messages"]
        system_content = messages_sent[0]["content"]
        assert "TCP" in system_content


# ---------------------------------------------------------------------------
# Skill hot-reload tests
# ---------------------------------------------------------------------------


class TestReloadSkills:
    def test_reload_skills_calls_invalidate_cache(self, agent):
        """reload_skills() must call skills_loader.invalidate_cache()."""
        with patch("edu_agent.skills_loader.invalidate_cache") as mock_inv:
            agent.reload_skills()
        mock_inv.assert_called_once()

    def test_reload_skills_does_not_raise(self, agent):
        """reload_skills() should complete without raising any exception."""
        # No patch – calls real invalidate_cache which just clears a dict
        agent.reload_skills()


# ---------------------------------------------------------------------------
# Manual context compression (trigger_context_compress)
# ---------------------------------------------------------------------------


class TestTriggerContextCompress:
    def test_without_session_store_is_noop(self, tmp_path, minimal_edu_settings: EduSettings) -> None:
        skills_dir = tmp_path / "sk"
        skills_dir.mkdir()
        config = AgentConfig(
            user_id="u",
            workspace=str(minimal_edu_settings.agent.workspace),
            skills_dir=str(skills_dir),
            memory_enabled=False,
            session_id="abc123456",
        )
        with patch("edu_agent.providers.runtime.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            a = EduAgent(config, settings=minimal_edu_settings, session_store=None)
        assert not a.has_context_manager
        assert not a.context_compression_active
        a.trigger_context_compress()

    def test_forced_compress_syncs_messages_and_store(
        self, tmp_path, minimal_edu_settings: EduSettings
    ) -> None:
        skills_dir = tmp_path / "sk"
        skills_dir.mkdir()
        config = AgentConfig(
            user_id="u",
            workspace=str(minimal_edu_settings.agent.workspace),
            skills_dir=str(skills_dir),
            memory_enabled=False,
        )
        paths = build_paths(
            minimal_edu_settings,
            workspace=config.workspace,
            skills_dir=config.skills_dir,
        )
        store = SessionStore(paths.sessions_db)
        cfg = ContextConfig(
            model_max_tokens=64_000,
            token_limit_percent=0.08,
            compression_enabled=True,
            summary_trigger_multiplier=1.0,
        )
        mgr = ContextManager(
            store,
            cfg,
            minimal_edu_settings,
            model_name="gpt-4o-mini",
            summarizer=lambda middle: None,
        )
        with patch("edu_agent.providers.runtime.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            a = EduAgent(
                config,
                settings=minimal_edu_settings,
                session_store=store,
                context_manager=mgr,
            )
        sid = a.config.session_id
        filler = "word " * 400
        for i in range(6):
            store.append_message(sid, {"role": "user", "content": f"{i}:{filler}"})
            store.append_message(sid, {"role": "assistant", "content": f"a{i}:{filler}"})
        a.messages = mgr.load_context(sid)
        assert a.has_context_manager
        assert a.context_compression_active
        assert len(a.messages) == 12
        a.trigger_context_compress()
        rows = store.list_messages(sid, limit=200)
        assert "system" in [r.metadata.role for r in rows]
        assert a.messages == mgr.load_context(sid)


class TestMemoryConsolidationLifecycle:
    def test_finalize_skips_second_llm_extract_after_threshold_consolidate(
        self, tmp_path, minimal_edu_settings: EduSettings
    ) -> None:
        skills_dir = tmp_path / "sk_mem"
        skills_dir.mkdir()
        config = AgentConfig(
            user_id="mem_user",
            workspace=str(minimal_edu_settings.agent.workspace),
            skills_dir=str(skills_dir),
            memory_enabled=True,
        )
        paths = build_paths(
            minimal_edu_settings,
            workspace=config.workspace,
            skills_dir=config.skills_dir,
        )
        store = SessionStore(paths.sessions_db)
        try:
            with patch("edu_agent.providers.runtime.OpenAI") as mock_openai_cls:
                mock_openai_cls.return_value = MagicMock()
                a = EduAgent(config, settings=minimal_edu_settings, session_store=store)
                a._client = mock_openai_cls.return_value
            a._memory_config.extraction_min_session_tokens = 1
            n_calls = {"c": 0}

            def _counting_extract(*_a, **_k):
                n_calls["c"] += 1
                return []

            a._memory_consolidator._extractor.extract_facts_from_session = _counting_extract  # type: ignore[method-assign]

            a._client.chat.completions.create.return_value = _make_response(
                [_make_choice(content="ok", finish_reason="stop")]
            )
            _run_turn(a, "hi")
            assert n_calls["c"] == 1
            a.finalize_memory_session()
            assert n_calls["c"] == 1
        finally:
            store.close()

