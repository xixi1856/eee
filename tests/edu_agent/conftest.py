"""Shared fixtures for EduAgent tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from edu_agent.config import (
    AgentDefaults,
    EduSettings,
    ProviderCredentials,
    ProvidersSettings,
    RuntimeSettings,
    ToolsSettings,
)
from edu_agent.paths import build_paths
from edu_agent.providers.runtime import resolve_provider_runtime
from edu_agent.runtime_context import TurnRuntimeContext, reset_current_runtime, set_current_runtime


@pytest.fixture()
def minimal_edu_settings(tmp_path: Path) -> EduSettings:
    """Isolated workspace + dashscope-shaped provider entry for unit tests."""
    root = tmp_path / "ws"
    root.mkdir()
    skills = root / "skills"
    skills.mkdir()
    return EduSettings(
        agent=AgentDefaults(
            workspace=root,
            model="test-model",
            provider="dashscope",
            temperature=0.1,
            max_tokens=2048,
            max_iterations=20,
            skills_dir="skills",
        ),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="sk-test-placeholder",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
        tools=ToolsSettings(),
        runtime=RuntimeSettings(),
    )


@pytest.fixture()
def with_turn_runtime(minimal_edu_settings: EduSettings):
    """Set ContextVar so execute_tool can call handlers that use get_current_runtime()."""
    paths = build_paths(minimal_edu_settings)
    pr = resolve_provider_runtime(minimal_edu_settings, None, "main")
    ctx = TurnRuntimeContext(
        settings=minimal_edu_settings,
        paths=paths,
        provider_runtime=pr,
        user_id="test",
        session_id="test-session",
    )
    tok = set_current_runtime(ctx)
    yield
    reset_current_runtime(tok)
