"""Tests for edu_agent.config_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from edu_agent.config_loader import load_settings, load_settings_from_file, resolve_env_vars


def test_resolve_env_vars_substitution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEST_EDU_XYZ", "hello")
    assert resolve_env_vars("pre-${TEST_EDU_XYZ}-post") == "pre-hello-post"


def test_load_settings_from_file_merges_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "custom.yaml"
    p.write_text(
        """
agent:
  provider: dashscope
  model: custom-model-xyz
providers:
  dashscope:
    api_key: "k-from-yaml"
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
""",
        encoding="utf-8",
    )
    s = load_settings_from_file(p)
    assert s.agent.model == "custom-model-xyz"
    assert s.providers.entries["dashscope"].api_key == "k-from-yaml"


def test_flat_providers_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "c.yaml"
    p.write_text(
        """
agent:
  provider: ollama
providers:
  ollama:
    api_key: ollama
    base_url: http://127.0.0.1:11434/v1
""",
        encoding="utf-8",
    )
    s = load_settings_from_file(p)
    assert "ollama" in s.providers.entries
