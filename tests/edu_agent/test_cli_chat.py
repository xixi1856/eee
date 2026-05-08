"""Tests for the edu chat CLI command (cli.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from edu_agent.cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_mock(replies: list[str]):
    """Return a mock EduAgent that produces *replies* in sequence."""
    agent = MagicMock()
    agent.config.session_id = "testsession"
    agent.run_turn.side_effect = replies
    return agent


# ---------------------------------------------------------------------------
# edu chat – basic flow
# ---------------------------------------------------------------------------

class TestChatCommand:
    def test_exit_on_quit_command(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock([])
            result = runner.invoke(cli, ["chat"], input="/quit\n")
        assert result.exit_code == 0
        assert "再见" in result.output

    def test_exit_on_exit_command(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock([])
            result = runner.invoke(cli, ["chat"], input="/exit\n")
        assert result.exit_code == 0

    def test_single_turn_reply_shown(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock(["TCP是传输控制协议。"])
            result = runner.invoke(cli, ["chat"], input="什么是TCP\n/quit\n")
        assert "TCP是传输控制协议。" in result.output

    def test_reset_command_clears_history(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            mock_agent = _make_agent_mock([])
            MockAgent.return_value = mock_agent
            runner.invoke(cli, ["chat"], input="/reset\n/quit\n")
        mock_agent.reset.assert_called_once()

    def test_empty_input_not_sent_to_agent(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            mock_agent = _make_agent_mock([])
            MockAgent.return_value = mock_agent
            runner.invoke(cli, ["chat"], input="   \n/quit\n")
        mock_agent.run_turn.assert_not_called()

    def test_agent_error_shown_without_crashing(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            mock_agent = _make_agent_mock([])
            mock_agent.run_turn.side_effect = RuntimeError("LLM unavailable")
            MockAgent.return_value = mock_agent
            result = runner.invoke(cli, ["chat"], input="问题\n/quit\n")
        assert result.exit_code == 0
        assert "错误" in result.output or "LLM unavailable" in result.output

    def test_custom_user_option_passed_to_config(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock([])
            runner.invoke(cli, ["chat", "--user", "alice"], input="/quit\n")
        _, kwargs = MockAgent.call_args
        # EduAgent is called with a positional AgentConfig argument
        config_arg = MockAgent.call_args[0][0]
        assert config_arg.user_id == "alice"

    def test_session_id_displayed_on_start(self):
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock([])
            result = runner.invoke(cli, ["chat"], input="/quit\n")
        assert "会话 ID:" in result.output

    def test_eof_exits_gracefully(self):
        """Simulates Ctrl-D (EOF) – the CLI should exit without an unhandled traceback."""
        runner = CliRunner()
        with patch("edu_agent.cli.EduAgent") as MockAgent:
            MockAgent.return_value = _make_agent_mock([])
            # No input at all → CliRunner sends EOF immediately
            result = runner.invoke(cli, ["chat"], input="")
        # Click may return exit code 1 on EOF; that's acceptable.
        # What matters is no unhandled exception traceback.
        assert result.exception is None or isinstance(result.exception, SystemExit)
