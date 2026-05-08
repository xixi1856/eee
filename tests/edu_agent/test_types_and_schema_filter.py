"""Tests for edu_agent.types and subagent schema filtering helpers."""

from __future__ import annotations

from edu_agent.subagent import _filter_schemas
from edu_agent.tools import TOOL_SCHEMAS
from edu_agent.types import ToolResult


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
    def test_only_whitelisted_tools_are_returned(self):
        allowed = ["knowledge_query", "generate_quiz"]
        schemas = _filter_schemas(allowed)
        names = [s["function"]["name"] for s in schemas]
        assert set(names) == set(allowed)

    def test_delegate_task_is_always_removed(self):
        schemas = _filter_schemas(["delegate_task", "knowledge_query"])
        names = [s["function"]["name"] for s in schemas]
        assert "delegate_task" not in names
        assert "knowledge_query" in names

    def test_unknown_tools_are_ignored(self):
        schemas = _filter_schemas(["not_exists", "knowledge_query"])
        names = [s["function"]["name"] for s in schemas]
        assert names == ["knowledge_query"]

    def test_filtered_names_must_exist_in_tool_schemas(self):
        known_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        names = [s["function"]["name"] for s in _filter_schemas(list(known_names))]
        assert set(names).issubset(known_names)
