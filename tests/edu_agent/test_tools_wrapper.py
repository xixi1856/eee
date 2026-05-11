"""Tests for edu_agent/tools.py — tool schema definitions and handler dispatch."""

import pytest

from edu_agent.tools import TOOL_SCHEMAS, execute_tool
from edu_agent.types import ToolResult


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestToolSchemas:
    def test_schemas_is_list(self):
        assert isinstance(TOOL_SCHEMAS, list)

    def test_required_tools_present(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for expected in (
            "knowledge_query",
            "generate_quiz",
            "build_mindmap",
            "parse_document",
            "ingest_document",
            "hint_generator",
            "score_essay",
            "evaluate_code",
        ):
            assert expected in names, f"missing tool: {expected}"

    def test_each_schema_has_type_function(self):
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]

    def test_knowledge_query_requires_question(self):
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "knowledge_query")
        required = schema["function"]["parameters"].get("required", [])
        assert "question" in required
        assert "sources" in required

    def test_generate_quiz_no_required_params(self):
        schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "generate_quiz")
        required = schema["function"]["parameters"].get("required", [])
        # All params optional – caller can invoke with zero arguments
        assert required == []


# ---------------------------------------------------------------------------
# execute_tool dispatch
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def test_unknown_tool_returns_error_result(self):
        result = execute_tool("nonexistent_tool", {})
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert "nonexistent_tool" in result.error

    def test_bad_args_returns_error_result(self):
        # knowledge_query requires 'question'; passing wrong kwarg triggers TypeError inside handler
        result = execute_tool("knowledge_query", {"wrong_param": "value"})
        assert isinstance(result, ToolResult)
        assert result.success is False


class TestKnowledgeQueryTool:
    @pytest.fixture(autouse=True)
    def _runtime(self, mocker):
        ctx = mocker.MagicMock()
        ctx.user_id = "00000000-0000-4000-8000-000000000001"
        ctx.course_id = None
        mocker.patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx)

    def test_success_path(self, mocker):
        mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {
                    "chunk_id": "x",
                    "text": "知识库答案",
                    "metadata": {},
                    "relevance_score": 1.0,
                    "origin": "personal",
                },
            ],
        )

        result = execute_tool(
            "knowledge_query",
            {"question": "什么是TCP？", "sources": "personal"},
        )
        assert isinstance(result, ToolResult)
        assert result.tool_name == "knowledge_query"
        assert result.success is True
        assert "知识库答案" in result.summary

    def test_engine_raises_returns_error(self, mocker):
        mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            side_effect=RuntimeError("RAG not initialised"),
        )

        result = execute_tool(
            "knowledge_query",
            {"question": "测试", "sources": "personal"},
        )
        assert result.success is False
        assert "RAG not initialised" in result.error

    def test_empty_answer_returns_no_info_message(self, mocker):
        mocker.patch("rag_mvp.engine.personal_retrieval_hits_sync", return_value=[])

        result = execute_tool(
            "knowledge_query",
            {"question": "测试", "sources": "personal"},
        )
        assert result.success is True
        assert "暂无" in result.summary

    def test_dict_answer_extracts_answer_key(self, mocker):
        mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {
                    "chunk_id": "c1",
                    "text": "字典答案",
                    "metadata": {},
                    "relevance_score": 0.9,
                    "origin": "personal",
                },
            ],
        )

        result = execute_tool(
            "knowledge_query",
            {"question": "测试", "sources": "personal"},
        )
        assert result.success is True
        assert "字典答案" in result.summary

    def test_default_mode_is_hybrid(self, mocker):
        mock_query = mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {"chunk_id": "a", "text": "answer", "metadata": {}, "relevance_score": 1.0, "origin": "personal"},
            ],
        )
        execute_tool(
            "knowledge_query",
            {"question": "test", "sources": "personal"},
        )
        mock_query.assert_called_once_with("test", mode="hybrid", top_k=5)

    def test_explicit_mode_passed_through(self, mocker):
        mock_query = mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {"chunk_id": "a", "text": "answer", "metadata": {}, "relevance_score": 1.0, "origin": "personal"},
            ],
        )
        execute_tool(
            "knowledge_query",
            {"question": "test", "mode": "local", "sources": "personal"},
        )
        mock_query.assert_called_once_with("test", mode="local", top_k=5)

    def test_sources_array_personal_only(self, mocker):
        mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {"chunk_id": "z", "text": "arr", "metadata": {}, "relevance_score": 1.0, "origin": "personal"},
            ],
        )
        result = execute_tool(
            "knowledge_query",
            {"question": "x", "sources": ["personal"]},
        )
        assert result.success is True
        assert "arr" in result.summary

    def test_sources_array_course_personal_is_all(self, mocker):
        ctx = mocker.MagicMock()
        ctx.user_id = "00000000-0000-4000-8000-000000000001"
        ctx.course_id = "00000000-0000-4000-8000-000000000099"
        mocker.patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx)
        mocker.patch(
            "edu_agent.tools.rag._sync_verify_and_query_course",
            return_value=[
                {
                    "chunk_id": "c1",
                    "text": "c",
                    "metadata": {"material_id": "00000000-0000-4000-8000-000000000088"},
                    "relevance_score": 1.0,
                },
            ],
        )
        mocker.patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            return_value=[
                {"chunk_id": "p1", "text": "p", "metadata": {}, "relevance_score": 0.9, "origin": "personal"},
            ],
        )
        mocker.patch("edu_agent.tools.rag._fetch_material_titles", return_value={})
        result = execute_tool(
            "knowledge_query",
            {"question": "both", "sources": ["course", "personal"]},
        )
        assert result.success is True


class TestGenerateQuizTool:
    def _make_fake_result(self, n: int = 2) -> dict:
        return {
            "generated_at": "2025-01-01T00:00:00",
            "file_filter": None,
            "total": n,
            "questions": [
                {
                    "type": "单选题",
                    "question": f"问题 {i}",
                    "options": ["A. 选项A", "B. 选项B"],
                    "answer": "A",
                    "explanation": "解析",
                }
                for i in range(1, n + 1)
            ],
        }

    def test_success_returns_formatted_markdown(self, mocker):
        mocker.patch(
            "rag_mvp.question_gen.generate",
            return_value=self._make_fake_result(2),
        )
        result = execute_tool("generate_quiz", {"count": 2})
        assert result.success is True
        assert "已生成 2 道练习题" in result.summary
        assert "问题 1" in result.summary

    def test_error_returns_failure(self, mocker):
        mocker.patch(
            "rag_mvp.question_gen.generate",
            side_effect=RuntimeError("no entities"),
        )
        result = execute_tool("generate_quiz", {})
        assert result.success is False
        assert "no entities" in result.error

    def test_count_clamped_to_max_20(self, mocker):
        mock_gen = mocker.patch(
            "rag_mvp.question_gen.generate",
            return_value=self._make_fake_result(5),
        )
        execute_tool("generate_quiz", {"count": 999})
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs["count"] == 20

    def test_count_clamped_to_min_1(self, mocker):
        mock_gen = mocker.patch(
            "rag_mvp.question_gen.generate",
            return_value=self._make_fake_result(1),
        )
        execute_tool("generate_quiz", {"count": 0})
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs["count"] == 1

    def test_mixed_type_uses_none_weights(self, mocker):
        mock_gen = mocker.patch(
            "rag_mvp.question_gen.generate",
            return_value=self._make_fake_result(1),
        )
        execute_tool("generate_quiz", {"count": 1, "question_type": "mixed"})
        assert mock_gen.call_args[1]["type_weights"] is None

    def test_specific_type_sets_single_weight(self, mocker):
        mock_gen = mocker.patch(
            "rag_mvp.question_gen.generate",
            return_value=self._make_fake_result(1),
        )
        execute_tool("generate_quiz", {"count": 1, "question_type": "fill_blank"})
        assert mock_gen.call_args[1]["type_weights"] == {"fill_blank": 1.0}


# ---------------------------------------------------------------------------
# ToolResult serialisation
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_success_to_content_returns_summary(self):
        r = ToolResult(tool_name="t", success=True, summary="ok")
        assert r.to_content() == "ok"

    def test_failure_to_content_includes_error(self):
        r = ToolResult(tool_name="t", success=False, summary="", error="boom")
        content = r.to_content()
        assert "失败" in content
        assert "boom" in content
