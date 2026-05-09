"""Tests for edu_agent.types and subagent schema filtering helpers."""

from __future__ import annotations

from edu_agent.config import EduSettings
from edu_agent.llm_tools import tool_specs_to_openai_tools
from edu_agent.subagent import _RECURSION_BLACKLIST
from edu_agent.tools import TOOL_SCHEMAS
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.types import ToolResult


def _openai_tools_for_allowed(settings: EduSettings, allowed_tools: list[str]) -> list[dict]:
    """Mirror ``SubAgent`` whitelist + OpenAI adapter (for tests)."""
    permitted = frozenset(allowed_tools) - _RECURSION_BLACKLIST
    specs = [s for s in toolset_registry.list_specs(settings) if s.name in permitted]
    return tool_specs_to_openai_tools(specs)


class TestToolResultToContent:
    def test_success_returns_summary(self):
        result = ToolResult(tool_name="knowledge_query", success=True, summary="ok")
        assert result.to_content() == "ok"

    def test_failure_includes_tool_name_and_error(self):
        result = ToolResult(
            tool_name="knowledge_query",
            success=False,
            summary="",
            error="RAG unavailable",
        )
        content = result.to_content()
        assert "knowledge_query" in content
        assert "RAG unavailable" in content


class TestFilterSchemas:
    def test_only_whitelisted_tools_are_returned(self, minimal_edu_settings: EduSettings):
        allowed = ["knowledge_query", "generate_quiz"]
        schemas = _openai_tools_for_allowed(minimal_edu_settings, allowed)
        names = [s["function"]["name"] for s in schemas]
        assert set(names) == set(allowed)

    def test_delegate_task_is_always_removed(self, minimal_edu_settings: EduSettings):
        schemas = _openai_tools_for_allowed(
            minimal_edu_settings, ["delegate_task", "knowledge_query"]
        )
        names = [s["function"]["name"] for s in schemas]
        assert "delegate_task" not in names
        assert "knowledge_query" in names

    def test_unknown_tools_are_ignored(self, minimal_edu_settings: EduSettings):
        schemas = _openai_tools_for_allowed(minimal_edu_settings, ["not_exists", "knowledge_query"])
        names = [s["function"]["name"] for s in schemas]
        assert names == ["knowledge_query"]

    def test_filtered_names_must_exist_in_tool_schemas(self, minimal_edu_settings: EduSettings):
        known_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        names = [
            s["function"]["name"]
            for s in _openai_tools_for_allowed(minimal_edu_settings, list(known_names))
        ]
        assert set(names).issubset(known_names)
