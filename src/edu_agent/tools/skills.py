"""Skill management tools.

Toolset: skills
Tools: list_skills, view_skill, manage_skill
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from edu_agent.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SCHEMA_LIST_SKILLS = {
    "name": "list_skills",
    "description": "列出当前所有可用的教学技能（name + description 索引）。",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_SCHEMA_VIEW_SKILL = {
    "name": "view_skill",
    "description": (
        "查看某个技能的完整 SKILL.md 内容（Tier1）。"
        "对于目录型技能，还可通过 file_path 读取 scripts/ 或 references/ 中的附件文件（Tier2）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名称（来自 list_skills）"},
            "file_path": {
                "type": "string",
                "description": "（可选）目录型技能内的附件相对路径，如 scripts/fetch.py",
            },
        },
        "required": ["name"],
    },
}

_SCHEMA_MANAGE_SKILL = {
    "name": "manage_skill",
    "description": (
        "创建或编辑技能文件。使用 create 创建新技能，edit 覆盖现有技能内容。"
        "技能保存后下一轮对话即可通过 list_skills / view_skill 使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit"],
                "description": "操作类型",
            },
            "name": {"type": "string", "description": "技能名称（英文，用于文件名）"},
            "content": {
                "type": "string",
                "description": "SKILL.md 的完整内容（含 frontmatter）",
            },
        },
        "required": ["action", "name", "content"],
    },
}


# ---------------------------------------------------------------------------
# Security patterns
# ---------------------------------------------------------------------------

_SKILL_THREAT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"os\.system\s*\(",
        r"subprocess\.(call|run|Popen)\s*\(",
        r"eval\s*\(",
        r"exec\s*\(",
        r"__import__\s*\(",
        r"open\s*\(.+['\"]w['\"]",
    ]
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_list_skills(args: dict, **kw) -> str:
    import os

    from edu_agent.skills_loader import load_skill_entries

    skills_dir = os.environ.get("EDU_SKILLS_DIR", "skills")
    entries = load_skill_entries(skills_dir)
    if not entries:
        return tool_result("暂无可用技能。")
    lines = ["**可用技能列表：**\n"]
    for e in entries:
        desc = f" — {e.description}" if e.description else ""
        badge = " *(始终注入)*" if e.always_inject else ""
        lines.append(f"• **{e.name}**{desc}{badge}")
    return tool_result(
        "\n".join(lines),
        payload=[e.name for e in entries],
    )


def _handle_view_skill(args: dict, **kw) -> str:
    import os

    from edu_agent.skills_loader import load_skill_entries, read_skill_file

    name = args.get("name", "")
    if not name:
        return tool_error("缺少必要参数：name")
    file_path: str = args.get("file_path", "")
    skills_dir = os.environ.get("EDU_SKILLS_DIR", "skills")
    entries = {e.name: e for e in load_skill_entries(skills_dir)}
    if name not in entries:
        return tool_error(f"技能不存在: {name}")
    entry = entries[name]
    if file_path:
        if entry.skill_dir is None:
            return tool_error("该技能为平铺格式，不支持附件访问")
        content = read_skill_file(entry.skill_dir, file_path)
        return tool_result(content)
    return tool_result(
        entry.body,
        payload={"name": name, "path": str(entry.path)},
    )


def _handle_manage_skill(args: dict, **kw) -> str:
    import os

    from edu_agent.skills_loader import invalidate_cache

    action = args.get("action", "")
    name = args.get("name", "")
    content = args.get("content", "")
    if not action or not name or not content:
        return tool_error("缺少必要参数：action、name、content")

    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return tool_error("技能名称只允许字母、数字、下划线和连字符")

    for pat in _SKILL_THREAT_PATTERNS:
        if pat.search(content):
            return tool_error(
                f"内容包含不允许的代码模式（安全扫描失败）: {pat.pattern}"
            )

    skills_dir = Path(os.environ.get("EDU_SKILLS_DIR", "skills"))
    skill_dir = skills_dir / name
    if skill_dir.is_dir():
        target = skill_dir / "SKILL.md"
    else:
        target = skills_dir / f"{name}.md"

    if action == "create" and target.exists():
        return tool_error(f"技能已存在，请使用 action='edit' 修改: {name}")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        invalidate_cache()
        return tool_result(
            f"技能 '{name}' 已{'创建' if action == 'create' else '更新'} → {target}",
            payload={"name": name, "path": str(target)},
        )
    except OSError as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="list_skills",
    schema=_SCHEMA_LIST_SKILLS,
    handler=_handle_list_skills,
    toolset="skills",
    emoji="📚",
)

registry.register(
    name="view_skill",
    schema=_SCHEMA_VIEW_SKILL,
    handler=_handle_view_skill,
    toolset="skills",
    emoji="👁️",
)

registry.register(
    name="manage_skill",
    schema=_SCHEMA_MANAGE_SKILL,
    handler=_handle_manage_skill,
    toolset="skills",
    emoji="✏️",
)
