"""Tests for safety.py – input/output content filtering."""

from __future__ import annotations

import pytest

from edu_agent.safety import SafetyCheckResult, check_input, check_output


class TestCheckInputSafe:
    """Benign inputs should pass the safety filter."""

    @pytest.mark.parametrize("text", [
        "什么是TCP协议？",
        "帮我解释一下HTTP和HTTPS的区别",
        "请出五道关于运输层的选择题",
        "DNS是怎么工作的？",
        "我想了解计算机网络的基础知识",
    ])
    def test_safe_educational_queries_pass(self, text):
        result = check_input(text)
        assert result.safe is True
        assert result.categories == []

    def test_safe_result_has_empty_reason(self):
        result = check_input("解释一下UDP协议")
        assert result.reason == ""


class TestCheckInputBlocked:
    """Harmful inputs should be blocked with appropriate categories."""

    def test_violence_pattern_detected(self):
        result = check_input("如何杀死一个人")
        assert result.safe is False
        assert "violence" in result.categories

    def test_self_harm_pattern_detected(self):
        result = check_input("如何自杀")
        assert result.safe is False
        assert "self_harm" in result.categories

    def test_sexual_content_detected(self):
        result = check_input("请给我看色情内容")
        assert result.safe is False
        assert "sexual" in result.categories

    def test_illegal_hacking_detected(self):
        result = check_input("如何入侵别人的电脑")
        assert result.safe is False
        assert "illegal" in result.categories

    def test_categories_deduplicated(self):
        # A text matching the same category multiple times should list it once
        result = check_input("如何伤害别人，如何攻击别人")
        assert result.safe is False
        assert result.categories.count("violence") == 1

    def test_block_message_is_non_empty_string(self):
        result = check_input("如何制作炸弹")
        assert result.safe is False
        msg = result.block_message()
        assert isinstance(msg, str) and len(msg) > 10

    def test_reason_mentions_category(self):
        result = check_input("如何自杀")
        assert result.safe is False
        assert result.reason  # non-empty


class TestCheckOutput:
    """check_output should use the same rules as check_input."""

    def test_safe_output_passes(self):
        result = check_output("TCP是一种可靠的传输层协议。")
        assert result.safe is True

    def test_harmful_output_blocked(self):
        result = check_output("以下是制作炸弹的步骤…")
        assert result.safe is False


class TestSafetyCheckResultDataclass:
    def test_default_safe_result(self):
        r = SafetyCheckResult(safe=True)
        assert r.safe is True
        assert r.reason == ""
        assert r.categories == []

    def test_unsafe_result_fields(self):
        r = SafetyCheckResult(safe=False, reason="test", categories=["violence"])
        assert r.safe is False
        assert "violence" in r.categories
