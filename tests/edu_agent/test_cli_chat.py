"""Tests for the edu ``chat`` CLI command (cli.py).

Uses real ``EduAgent`` + Gateway + ``CLIChannelAdapter``; stubs OpenAI at
``edu_agent.agent.build_{async_,}openai_client`` so no network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from openai import APIConnectionError

from edu_agent.cli import cli
from edu_agent.config import (
    AgentDefaults,
    EduSettings,
    ProviderCredentials,
    ProvidersSettings,
    RuntimeSettings,
    ToolsSettings,
)
from edu_agent.paths import build_paths
from edu_agent.sessions.store import SessionStore

from tests.edu_agent.offline_llm import (
    patch_agent_openai_factories,
    stream_factory_fixed_text,
)


@pytest.fixture(autouse=True)
def _patch_cli_load_settings(tmp_path: Path):
    root = tmp_path / "ws_cli"
    root.mkdir()
    (root / "skills").mkdir()
    st = EduSettings(
        agent=AgentDefaults(
            workspace=root,
            model="m",
            provider="dashscope",
            skills_dir="skills",
        ),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="k",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
        tools=ToolsSettings(),
        runtime=RuntimeSettings(),
    )
    with patch("edu_agent.cli.load_settings", return_value=st):
        yield


class TestChatCommand:
    def test_exit_on_quit_command(self):
        runner = CliRunner()
        with patch_agent_openai_factories():
            result = runner.invoke(cli, ["chat"], input="/quit\n")
        assert result.exit_code == 0
        assert "再见" in result.output

    def test_exit_on_exit_command(self):
        runner = CliRunner()
        with patch_agent_openai_factories():
            result = runner.invoke(cli, ["chat"], input="/exit\n")
        assert result.exit_code == 0

    def test_single_turn_reply_shown(self, tmp_path: Path):
        runner = CliRunner()
        with patch_agent_openai_factories(
            stream_factory=stream_factory_fixed_text("TCP是传输控制协议。")
        ):
            result = runner.invoke(cli, ["chat"], input="什么是TCP\n/quit\n")
        assert result.exit_code == 0
        assert "TCP是传输控制协议。" in result.output

    def test_reset_command_clears_history(self, tmp_path: Path):
        """Gateway ``/reset`` allocates a new session row (observable via CLI output)."""
        runner = CliRunner()
        with patch_agent_openai_factories(stream_factory=stream_factory_fixed_text("hi")):
            result = runner.invoke(cli, ["chat"], input="hello\n/reset\n/quit\n")
        assert result.exit_code == 0
        assert "[新会话]" in result.output or "新会话" in result.output

    def test_empty_input_not_sent_to_agent(self, tmp_path: Path):
        runner = CliRunner()
        n_async = {"c": 0}
        orig = None

        async def _counting_stream(*a: object, **k: object) -> object:
            n_async["c"] += 1

            async def _g():
                from tests.edu_agent.offline_llm import async_iter_text_response

                async for c in async_iter_text_response("x"):
                    yield c

            return _g()

        with patch_agent_openai_factories(stream_factory=_counting_stream):
            runner.invoke(cli, ["chat"], input="   \n/quit\n")
        assert n_async["c"] == 0

    def test_agent_error_shown_without_crashing(self, tmp_path: Path):
        runner = CliRunner()

        async def _boom(*_a: object, **_k: object) -> object:
            raise APIConnectionError(request=MagicMock())

        with patch_agent_openai_factories(stream_factory=_boom):
            result = runner.invoke(cli, ["chat"], input="问题\n/quit\n")
        assert result.exit_code == 0
        assert "错误" in result.output or "Connection" in result.output or "LLM" in result.output

    def test_custom_user_option_passed_to_config(self, tmp_path: Path):
        runner = CliRunner()
        st = None
        with patch("edu_agent.cli.load_settings") as m_ls:
            root = tmp_path / "ws2"
            root.mkdir()
            (root / "skills").mkdir()
            st = EduSettings(
                agent=AgentDefaults(
                    workspace=root,
                    model="m",
                    provider="dashscope",
                    skills_dir="skills",
                ),
                providers=ProvidersSettings(
                    entries={
                        "dashscope": ProviderCredentials(
                            api_key="k",
                            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                        ),
                    },
                ),
                tools=ToolsSettings(),
                runtime=RuntimeSettings(),
            )
            m_ls.return_value = st
            with patch_agent_openai_factories():
                runner.invoke(cli, ["chat", "--user", "alice"], input="/quit\n")
        assert st is not None
        paths = build_paths(st)
        store = SessionStore(paths.sessions_db)
        try:
            rows = store.search_sessions(user_id="alice", limit=10)
            assert any(r.metadata.user_id == "alice" for r in rows)
        finally:
            store.close()

    def test_session_id_displayed_on_start(self):
        runner = CliRunner()
        with patch_agent_openai_factories():
            result = runner.invoke(cli, ["chat"], input="/quit\n")
        assert "会话 ID:" in result.output

    def test_eof_exits_gracefully(self):
        runner = CliRunner()
        with patch_agent_openai_factories():
            result = runner.invoke(cli, ["chat"], input="")
        assert result.exception is None or isinstance(result.exception, SystemExit)
