"""Load teaching-strategy skill files from a directory.

Supports two skill formats:
  1. Flat file:      skills/{name}.md
  2. Directory-based: skills/{name}/SKILL.md   (hermes-agent style)
     └── scripts/   (optional scripts accessible via view_skill)
     └── references/ (optional reference docs)

Flat files remain supported for backward compatibility.  Directory-based
skills take priority when a name collision occurs.

Skill files may contain optional YAML frontmatter (compatible with
agentskills.io specification):

    ---
    name: socratic
    description: 苏格拉底式引导
    version: 1.0.0
    triggers: [概念性问题, 原理探究]
    always_inject: false
    ---
    ... markdown body ...
"""

from __future__ import annotations

import logging
import os
import platform
import re
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

_CACHE: dict[str, str] = {}

# ---------------------------------------------------------------------------
# SkillEntry — structured representation of a discovered skill
# ---------------------------------------------------------------------------

class SkillEntry(NamedTuple):
    """Metadata + content for a single skill.

    Level-0 index fields (always populated, minimal token cost):
        name, description, version, always_inject, requires_tools

    Level-1 content (loaded on demand via view_skill):
        body

    Level-2 assets (directory-based skills only):
        scripts, skill_dir
    """
    name: str               # skill identifier (frontmatter > stem)
    description: str        # one-line summary for Tier0 index
    version: str            # semver string, default "1.0.0"
    body: str               # full SKILL.md / .md text (frontmatter stripped)
    path: Path              # path to the SKILL.md / .md file
    skill_dir: Path | None  # root dir for directory-based skills, else None
    always_inject: bool     # if True, full body always in system prompt
    # --- Hermes-style gating fields (Level-0 metadata) ---
    requires_tools: list[str]   # skill hidden when any required tool is absent
    requires_env: list[str]     # env vars that must be present
    requires_config: list[str]  # reserved for future config-gating support
    platforms: list[str]        # allowed platforms (windows/linux/darwin)
    scripts: list[Path]         # executable scripts under skill_dir/scripts/
    raw_meta: dict              # full parsed frontmatter (for future extensions)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (meta_dict, body).

    Falls back to an empty dict and the full content when no frontmatter is
    found.  Uses a simple line-by-line parser to avoid a PyYAML dependency.
    """
    m = _FM_RE.match(content)
    if not m:
        return {}, content

    raw = m.group(1)
    body = content[m.end():]
    meta: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        # Handle inline lists: [a, b, c]
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            meta[key] = items
        elif val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"
        elif val:
            meta[key] = val
    return meta, body


# ---------------------------------------------------------------------------
# Low-level file loader with cache
# ---------------------------------------------------------------------------

def _load_raw(path: Path) -> str:
    """Return raw file contents with in-memory caching."""
    key = str(path)
    if key not in _CACHE:
        try:
            _CACHE[key] = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read skill file %s: %s", path, exc)
            _CACHE[key] = ""
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Public helpers kept for backward compatibility
# ---------------------------------------------------------------------------

def load_skill(path: Path) -> str:
    """Return the raw contents of a single skill file (with caching)."""
    return _load_raw(path)


def load_all_skills(skills_dir: str | Path = "skills") -> dict[str, str]:
    """Backward-compat wrapper → returns {name: full_raw_content}.

    Callers that only need the body text (e.g. existing prompt_builder) can
    continue using this.  New callers should use ``load_skill_entries()``.
    """
    return {e.name: e.body for e in load_skill_entries(skills_dir)}


def _current_platform() -> str:
    p = platform.system().lower()
    if p.startswith("win"):
        return "windows"
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    return p


def _is_eligible(
    requires_env: list[str],
    platforms: list[str],
) -> bool:
    if platforms and _current_platform() not in {p.lower() for p in platforms}:
        return False
    for env_name in requires_env:
        if not os.environ.get(env_name):
            return False
    return True


# ---------------------------------------------------------------------------
# Primary API: load_skill_entries
# ---------------------------------------------------------------------------

def load_skill_entries(skills_dir: str | Path = "skills") -> list[SkillEntry]:
    """Discover all skills and return a list of ``SkillEntry`` objects.

    Discovery order (higher index = higher priority):
      1. Flat ``.md`` files directly in *skills_dir*
      2. Directory-based skills ({name}/SKILL.md) — override same-name flat files

    Within each tier, entries are sorted alphabetically by name.
    EDUCATOR is always placed first among always_inject skills.
    """
    directory = Path(skills_dir)
    if not directory.is_dir():
        logger.debug("Skills directory not found: %s", directory)
        return []

    entries: dict[str, SkillEntry] = {}

    # --- Tier 1: flat .md files ---
    for md_file in sorted(directory.glob("*.md")):
        raw = _load_raw(md_file)
        if not raw:
            continue
        meta, body = parse_frontmatter(raw)
        stem = md_file.stem
        name = str(meta.get("name", stem))
        requires_tools = meta.get("requires_tools", [])
        if isinstance(requires_tools, str):
            requires_tools = [requires_tools] if requires_tools else []
        requires_env = meta.get("requires_env", [])
        if isinstance(requires_env, str):
            requires_env = [requires_env] if requires_env else []
        requires_config = meta.get("requires_config", [])
        if isinstance(requires_config, str):
            requires_config = [requires_config] if requires_config else []
        platforms = meta.get("platforms", meta.get("os", []))
        if isinstance(platforms, str):
            platforms = [platforms] if platforms else []
        if not _is_eligible(requires_env, platforms):
            continue
        entries[name] = SkillEntry(
            name=name,
            description=str(meta.get("description", "")),
            version=str(meta.get("version", "1.0.0")),
            body=body.strip(),
            path=md_file,
            skill_dir=None,
            always_inject=bool(meta.get("always_inject", name.upper() == "EDUCATOR")),
            requires_tools=requires_tools,
            requires_env=requires_env,
            requires_config=requires_config,
            platforms=platforms,
            scripts=[],
            raw_meta=meta,
        )

    # --- Tier 2: directory-based skills ({name}/SKILL.md) ---
    for skill_md in sorted(directory.glob("*/SKILL.md")):
        raw = _load_raw(skill_md)
        if not raw:
            continue
        meta, body = parse_frontmatter(raw)
        stem = skill_md.parent.name
        name = str(meta.get("name", stem))
        requires_tools = meta.get("requires_tools", [])
        if isinstance(requires_tools, str):
            requires_tools = [requires_tools] if requires_tools else []
        requires_env = meta.get("requires_env", [])
        if isinstance(requires_env, str):
            requires_env = [requires_env] if requires_env else []
        requires_config = meta.get("requires_config", [])
        if isinstance(requires_config, str):
            requires_config = [requires_config] if requires_config else []
        platforms = meta.get("platforms", meta.get("os", []))
        if isinstance(platforms, str):
            platforms = [platforms] if platforms else []
        if not _is_eligible(requires_env, platforms):
            continue
        # Discover scripts/*.py under the skill directory
        scripts_dir = skill_md.parent / "scripts"
        scripts = sorted(scripts_dir.glob("*.py")) if scripts_dir.is_dir() else []
        entries[name] = SkillEntry(
            name=name,
            description=str(meta.get("description", "")),
            version=str(meta.get("version", "1.0.0")),
            body=body.strip(),
            path=skill_md,
            skill_dir=skill_md.parent,
            always_inject=bool(meta.get("always_inject", False)),
            requires_tools=requires_tools,
            requires_env=requires_env,
            requires_config=requires_config,
            platforms=platforms,
            scripts=scripts,
            raw_meta=meta,
        )

    # Sort: always_inject first (EDUCATOR at top), then alpha
    result = sorted(
        entries.values(),
        key=lambda e: (0 if e.always_inject and e.name.upper() == "EDUCATOR" else
                       1 if e.always_inject else 2,
                       e.name.lower()),
    )
    return result


def read_skill_file(skill_dir: Path, file_path: str) -> str:
    """Read a file inside a directory-based skill (Tier2 access).

    Performs path traversal check: *file_path* must resolve inside *skill_dir*.
    Returns the file contents, or an error string if access is denied / not found.
    """
    resolved = (skill_dir / file_path).resolve()
    if not str(resolved).startswith(str(skill_dir.resolve())):
        return f"[错误] 路径越界，拒绝访问: {file_path}"
    if not resolved.exists():
        return f"[错误] 文件不存在: {file_path}"
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"


def invalidate_cache() -> None:
    """Clear the in-memory skill cache (useful for tests and hot-reload)."""
    _CACHE.clear()
