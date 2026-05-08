"""Tests for Phase 3 evaluation tools: hint_generator, score_essay, evaluate_code."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from edu_agent.tools import execute_tool
from edu_agent.types import ToolResult


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mock_llm(return_value: str):
    """Patch edu_agent.tools.eval._call_llm to return *return_value*."""
    return patch("edu_agent.tools.eval._call_llm", return_value=return_value)


# ---------------------------------------------------------------------------
# hint_generator
# ---------------------------------------------------------------------------


class TestHintGeneratorTool:
    def test_returns_hint_on_success(self):
        with _mock_llm("思考一下这两个协议在可靠性上的差异"):
            result = execute_tool("hint_generator", {"question": "TCP和UDP有什么区别？"})
        assert result.success is True
        assert "可靠性" in result.summary

    def test_level_clamped_to_min_1(self):
        """level=0 should be silently clamped to 1, not raise."""
        with _mock_llm("hint") as mock_fn:
            result = execute_tool("hint_generator", {"question": "q", "level": 0})
        assert result.success is True

    def test_level_clamped_to_max_3(self):
        """level=99 should be silently clamped to 3, not raise."""
        with _mock_llm("hint") as mock_fn:
            result = execute_tool("hint_generator", {"question": "q", "level": 99})
        assert result.success is True

    def test_context_included_in_prompt(self):
        captured: dict = {}

        def fake_llm(prompt: str, system: str = "") -> str:
            captured["prompt"] = prompt
            return "hint"

        with patch("edu_agent.tools.eval._call_llm", side_effect=fake_llm):
            execute_tool(
                "hint_generator",
                {"question": "q", "context": "相关背景ABC"},
            )
        assert "相关背景ABC" in captured["prompt"]

    def test_llm_error_returns_failure(self):
        with patch("edu_agent.tools.eval._call_llm", side_effect=RuntimeError("network")):
            result = execute_tool("hint_generator", {"question": "q"})
        assert result.success is False
        assert "network" in result.error


# ---------------------------------------------------------------------------
# score_essay
# ---------------------------------------------------------------------------


class TestScoreEssayTool:
    def test_success_with_json_response(self):
        payload = json.dumps(
            {
                "score": 82,
                "summary": "整体不错",
                "strengths": "逻辑清晰",
                "improvements": "需补充实例",
            }
        )
        with _mock_llm(payload):
            result = execute_tool(
                "score_essay",
                {"question": "描述TCP三次握手", "student_answer": "客户端发SYN..."},
            )
        assert result.success is True
        assert "82" in result.summary
        assert "逻辑清晰" in result.summary

    def test_non_json_reply_falls_back_to_plain_text(self):
        """When LLM returns non-JSON, the raw text is used as summary."""
        with _mock_llm("总分：75分，答题方向正确但细节不足"):
            result = execute_tool(
                "score_essay",
                {"question": "q", "student_answer": "a"},
            )
        assert result.success is True
        assert result.summary  # not empty

    def test_rubric_included_in_prompt(self):
        captured: dict = {}

        def fake_llm(prompt: str, system: str = "") -> str:
            captured["prompt"] = prompt
            return "{}"

        with patch("edu_agent.tools.eval._call_llm", side_effect=fake_llm):
            execute_tool(
                "score_essay",
                {
                    "question": "q",
                    "student_answer": "a",
                    "rubric": "必须包含三个步骤",
                },
            )
        assert "必须包含三个步骤" in captured["prompt"]

    def test_llm_error_returns_failure(self):
        with patch("edu_agent.tools.eval._call_llm", side_effect=Exception("timeout")):
            result = execute_tool(
                "score_essay",
                {"question": "q", "student_answer": "a"},
            )
        assert result.success is False
        assert "timeout" in result.error


# ---------------------------------------------------------------------------
# evaluate_code
# ---------------------------------------------------------------------------


class TestEvaluateCodeTool:
    def test_success_returns_feedback(self):
        with _mock_llm("代码逻辑正确，建议添加注释以提升可读性"):
            result = execute_tool(
                "evaluate_code",
                {"code": "def add(a, b): return a+b", "task_description": "实现加法"},
            )
        assert result.success is True
        assert "注释" in result.summary

    def test_default_language_python_in_prompt(self):
        captured: dict = {}

        def fake_llm(prompt: str, system: str = "") -> str:
            captured["prompt"] = prompt
            return "ok"

        with patch("edu_agent.tools.eval._call_llm", side_effect=fake_llm):
            execute_tool(
                "evaluate_code",
                {"code": "x = 1", "task_description": "赋值变量"},
            )
        assert "python" in captured["prompt"].lower()

    def test_custom_language_in_prompt(self):
        captured: dict = {}

        def fake_llm(prompt: str, system: str = "") -> str:
            captured["prompt"] = prompt
            return "ok"

        with patch("edu_agent.tools.eval._call_llm", side_effect=fake_llm):
            execute_tool(
                "evaluate_code",
                {
                    "code": "int x = 1;",
                    "task_description": "声明整型变量",
                    "language": "java",
                },
            )
        assert "java" in captured["prompt"].lower()

    def test_llm_error_returns_failure(self):
        with patch("edu_agent.tools.eval._call_llm", side_effect=Exception("err")):
            result = execute_tool(
                "evaluate_code",
                {"code": "x", "task_description": "t"},
            )
        assert result.success is False
        assert "err" in result.error
