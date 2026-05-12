"""Tests for provider registry + runtime resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from edu_agent.config import AgentDefaults, EduSettings, ProviderCredentials, ProvidersSettings
from edu_agent.providers.registry import get_provider_spec, resolve_provider_id
from edu_agent.providers.runtime import build_openai_client, resolve_provider_runtime
from edu_agent.types import AgentConfig


def test_resolve_provider_id_aliases():
    assert resolve_provider_id("qwen") == "dashscope"
    assert resolve_provider_id("DashScope") == "dashscope"


def test_get_provider_spec_openai():
    spec = get_provider_spec("openai")
    assert spec.default_base_url is not None
    assert "openai.com" in spec.default_base_url


def test_resolve_provider_runtime_model_override():
    settings = EduSettings(
        agent=AgentDefaults(
            workspace=Path("."),
            model="base-model",
            provider="dashscope",
        ),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="k",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
    )
    rt = resolve_provider_runtime(
        settings,
        AgentConfig(model="override-model"),
        "main",
    )
    assert rt.model == "override-model"
    assert rt.provider_id == "dashscope"


def test_resolve_auxiliary_matches_main_for_a1():
    settings = EduSettings(
        agent=AgentDefaults(workspace=Path("."), provider="ollama"),
        providers=ProvidersSettings(
            entries={
                "ollama": ProviderCredentials(api_key="ollama", base_url="http://127.0.0.1:11434/v1"),
            },
        ),
    )
    main = resolve_provider_runtime(settings, None, "main")
    aux = resolve_provider_runtime(settings, None, "auxiliary")
    assert main.model == aux.model
    assert main.api_key == aux.api_key


def test_openai_compatible_requires_base_url():
    settings = EduSettings(
        agent=AgentDefaults(workspace=Path("."), provider="openai_compatible", model="m"),
        providers=ProvidersSettings(
            entries={"openai_compatible": ProviderCredentials(api_key="k")},
        ),
    )
    with pytest.raises(ValueError, match="base_url"):
        resolve_provider_runtime(settings, None, "main")


def test_resolve_provider_runtime_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    settings = EduSettings(
        agent=AgentDefaults(workspace=Path("."), provider="dashscope", model="m"),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
    )
    with pytest.raises(ValueError, match="Missing api_key"):
        resolve_provider_runtime(settings, None, "main")


def test_resolve_provider_runtime_llm_extra_body():
    settings = EduSettings(
        agent=AgentDefaults(
            workspace=Path("."),
            model="m",
            provider="dashscope",
            llm_extra_body={"thinking": {"type": "disabled"}},
        ),
        providers=ProvidersSettings(
            entries={
                "dashscope": ProviderCredentials(
                    api_key="k",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            },
        ),
    )
    rt = resolve_provider_runtime(settings, None, "main")
    assert rt.llm_extra_body == {"thinking": {"type": "disabled"}}


def test_build_openai_client_delegates_to_sdk():
    from edu_agent.providers.types import ResolvedProviderRuntime

    rt = ResolvedProviderRuntime(
        provider_id="dashscope",
        model="m",
        api_key="k",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_mode="chat_completions",
        client_kind="openai",
        temperature=0.1,
        max_tokens=100,
    )
    with patch("edu_agent.providers.runtime.OpenAI") as MockO:
        MockO.return_value = MagicMock()
        c = build_openai_client(rt)
        MockO.assert_called_once()
        assert c is MockO.return_value
