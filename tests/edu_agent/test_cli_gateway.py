"""CLI → Gateway E2E smoke (no LLM; immediate /quit)."""

from __future__ import annotations

from click.testing import CliRunner

from edu_agent.cli import cli


def test_cli_chat_gateway_quit():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["chat", "--user", "gwtest", "--approve-all", "--gateway-mode"],
        input="/quit\n",
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "再见" in result.output or "退出" in result.output
