"""Tests for MemoryCoordinator, EduMemoryManager, and memory output scrubber."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from edu_agent.memory.coordinator import MemoryCoordinator
from edu_agent.memory.manager import EduMemoryManager
from edu_agent.memory.models import MemoryConfig
from edu_agent.memory.output_scrubber import sanitize_completed_assistant_output
from edu_agent.memory.provider import EduMemoryProvider, NullExternalMemoryProvider


def test_build_retrieved_memory_block_calls_retriever_and_formats() -> None:
    retriever = MagicMock()
    c = MagicMock()
    c.name = "分数化简"
    c.mastery_level = 0.6
    retriever.get_relevant_concepts.return_value = [c]
    consolidator = MagicMock()
    cfg = MemoryConfig(memory_inject_max_chars=10_000)
    coord = MemoryCoordinator(retriever, cfg, consolidator)

    out = coord.build_retrieved_memory_block("user-1", "help with fractions")

    retriever.get_relevant_concepts.assert_called_once()
    args, kwargs = retriever.get_relevant_concepts.call_args
    assert args[0] == "user-1"
    assert "fractions" in args[1]["last_user_message"]
    assert "掌握度 0.60" in out
    assert "分数化简" in out


def test_build_retrieved_memory_block_respects_char_cap() -> None:
    retriever = MagicMock()
    c = MagicMock()
    c.name = "X" * 50
    c.mastery_level = 0.1
    retriever.get_relevant_concepts.return_value = [c]
    consolidator = MagicMock()
    cfg = MemoryConfig(memory_inject_max_chars=20)
    coord = MemoryCoordinator(retriever, cfg, consolidator)
    out = coord.build_retrieved_memory_block("u", "q")
    assert len(out) <= 21
    assert out.endswith("…")


def test_build_retrieved_memory_block_empty_hint() -> None:
    retriever = MagicMock()
    coord = MemoryCoordinator(retriever, MemoryConfig(), MagicMock())
    assert coord.build_retrieved_memory_block("u", "   ") == ""
    retriever.get_relevant_concepts.assert_not_called()


def test_should_run_threshold_consolidate(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever = MagicMock()
    consolidator = MagicMock()
    cfg = MemoryConfig(extraction_min_session_tokens=100)
    coord = MemoryCoordinator(retriever, cfg, consolidator)

    monkeypatch.setattr(
        "edu_agent.memory.coordinator.estimate_messages_tokens_rough",
        lambda _msgs: 50,
    )
    assert coord.should_run_threshold_consolidate([{"role": "user", "content": "x"}]) is False

    monkeypatch.setattr(
        "edu_agent.memory.coordinator.estimate_messages_tokens_rough",
        lambda _msgs: 150,
    )
    assert coord.should_run_threshold_consolidate([{"role": "user", "content": "x"}]) is True


def test_consolidate_session_delegates() -> None:
    retriever = MagicMock()
    consolidator = MagicMock()
    coord = MemoryCoordinator(retriever, MemoryConfig(), consolidator)
    messages = MagicMock()
    coord.consolidate_session("u", "sid", messages, force_extract=True)
    consolidator.consolidate_session.assert_called_once_with(
        "u", "sid", messages, force_extract=True, extract_after_seq=None
    )


class _StubProvider(EduMemoryProvider):
    def __init__(self, name: str, prefetch_text: str = "", *, available: bool = True) -> None:
        self._n = name
        self._prefetch_text = prefetch_text
        self._available = available

    @property
    def name(self) -> str:
        return self._n

    def is_available(self) -> bool:
        return self._available

    def initialize(self, session_id: str, **kwargs: object) -> None:
        return

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._prefetch_text

    def get_tool_schemas(self) -> list[dict]:
        return []


def test_edu_memory_manager_prefetch_all_joins_providers() -> None:
    mgr = EduMemoryManager()
    mgr.add_provider(_StubProvider("builtin", "alpha"))
    mgr.add_provider(_StubProvider("honcho", "beta"))
    assert mgr.prefetch_all("q") == "alpha\n\nbeta"


def test_edu_memory_manager_skips_unavailable() -> None:
    mgr = EduMemoryManager()
    mgr.add_provider(_StubProvider("builtin", "only", available=True))
    mgr.add_provider(NullExternalMemoryProvider())
    assert mgr.prefetch_all("q") == "only"


def test_edu_memory_manager_rejects_second_external() -> None:
    mgr = EduMemoryManager()
    assert mgr.add_provider(_StubProvider("builtin", "a"))
    assert mgr.add_provider(_StubProvider("ext_a", "b"))
    assert mgr.add_provider(_StubProvider("ext_b", "c")) is False
    assert mgr.prefetch_all("q") == "a\n\nb"


def test_sanitize_completed_assistant_output_noop_when_flag_off() -> None:
    text = "## 相关长期记忆（检索注入）\n- leak"
    assert sanitize_completed_assistant_output(text, memory_injection_used=False) == text


def test_sanitize_completed_assistant_output_strips_echoed_section() -> None:
    text = (
        "正常回答。\n"
        "## 相关长期记忆（检索注入）\n"
        "- 不应出现在输出中的回声\n"
        "## 其他章节\n"
        "继续"
    )
    cleaned = sanitize_completed_assistant_output(text, memory_injection_used=True)
    assert "回声" not in cleaned
    assert "正常回答" in cleaned
    assert "## 其他章节" in cleaned
