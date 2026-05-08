"""Tests for prompt_builder.py and skills_loader.py."""

from pathlib import Path

import pytest

from edu_agent.prompt_builder import build_system_prompt
from edu_agent.skills_loader import invalidate_cache, load_all_skills, load_skill


# ---------------------------------------------------------------------------
# skills_loader tests
# ---------------------------------------------------------------------------

class TestLoadSkill:
    def test_loads_file_content(self, tmp_path):
        f = tmp_path / "TEST.md"
        f.write_text("# Test Skill\nContent here.", encoding="utf-8")
        assert load_skill(f) == "# Test Skill\nContent here."

    def test_missing_file_returns_empty_string(self, tmp_path):
        invalidate_cache()
        result = load_skill(tmp_path / "missing.md")
        assert result == ""

    def test_result_is_cached(self, tmp_path):
        invalidate_cache()
        f = tmp_path / "skill.md"
        f.write_text("original", encoding="utf-8")
        first = load_skill(f)
        f.write_text("modified", encoding="utf-8")
        second = load_skill(f)
        assert first == second == "original"

    def test_invalidate_cache_clears_entries(self, tmp_path):
        invalidate_cache()
        f = tmp_path / "skill.md"
        f.write_text("v1", encoding="utf-8")
        load_skill(f)
        invalidate_cache()
        f.write_text("v2", encoding="utf-8")
        assert load_skill(f) == "v2"


class TestLoadAllSkills:
    def test_empty_directory_returns_empty_dict(self, tmp_path):
        invalidate_cache()
        result = load_all_skills(tmp_path)
        assert result == {}

    def test_missing_directory_returns_empty_dict(self, tmp_path):
        invalidate_cache()
        result = load_all_skills(tmp_path / "nonexistent")
        assert result == {}

    def test_loads_all_md_files(self, tmp_path):
        invalidate_cache()
        (tmp_path / "A.md").write_text("skill A", encoding="utf-8")
        (tmp_path / "B.md").write_text("skill B", encoding="utf-8")
        (tmp_path / "not_md.txt").write_text("ignored", encoding="utf-8")

        result = load_all_skills(tmp_path)
        assert set(result.keys()) == {"A", "B"}
        assert result["A"] == "skill A"

    def test_empty_md_file_excluded(self, tmp_path):
        invalidate_cache()
        (tmp_path / "empty.md").write_text("", encoding="utf-8")
        result = load_all_skills(tmp_path)
        assert "empty" not in result


# ---------------------------------------------------------------------------
# build_system_prompt tests
# ---------------------------------------------------------------------------

class TestBuildSystemPrompt:
    def test_returns_non_empty_string(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(skills_dir=tmp_path)
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_uses_educator_md_when_present(self, tmp_path):
        invalidate_cache()
        (tmp_path / "EDUCATOR.md").write_text("# 自定义教师角色", encoding="utf-8")
        prompt = build_system_prompt(skills_dir=tmp_path)
        assert "自定义教师角色" in prompt

    def test_falls_back_to_default_persona_when_no_educator_md(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(skills_dir=tmp_path)
        # Default persona contains this Chinese phrase
        assert "教学助手" in prompt

    def test_safety_block_always_present(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(skills_dir=tmp_path)
        assert "安全准则" in prompt

    def test_tool_guidance_always_present(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(skills_dir=tmp_path)
        assert "knowledge_query" in prompt

    def test_learner_profile_included_when_provided(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(
            skills_dir=tmp_path,
            learner_profile_summary="学习者已掌握基础概念，但对应用层理解薄弱。",
        )
        assert "学习者当前状态" in prompt
        assert "应用层" in prompt

    def test_learner_profile_omitted_when_empty(self, tmp_path):
        invalidate_cache()
        prompt = build_system_prompt(skills_dir=tmp_path, learner_profile_summary="")
        assert "学习者当前状态" not in prompt

    def test_extra_skill_files_included(self, tmp_path):
        invalidate_cache()
        (tmp_path / "socratic.md").write_text("# 苏格拉底策略\n测试内容", encoding="utf-8")
        prompt = build_system_prompt(skills_dir=tmp_path)
        # Skills without always_inject appear in the <available_skills> index, not full text
        assert "socratic" in prompt

    def test_educator_md_not_duplicated(self, tmp_path):
        """EDUCATOR.md should appear exactly once — as the persona block, not also in skills."""
        invalidate_cache()
        (tmp_path / "EDUCATOR.md").write_text("唯一内容", encoding="utf-8")
        prompt = build_system_prompt(skills_dir=tmp_path)
        assert prompt.count("唯一内容") == 1
