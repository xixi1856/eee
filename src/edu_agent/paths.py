"""Unified path layout under workspace (EduPaths)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edu_agent.config import EduSettings


def _expand_path(p: str | Path) -> Path:
    path = Path(p)
    return path.expanduser().resolve()


@dataclass(frozen=True)
class EduPaths:
    """All user-data and artefact paths derived from workspace + overrides."""

    workspace: Path
    sessions_dir: Path
    profiles_dir: Path
    memory_dir: Path
    skills_dir: Path
    logs_dir: Path
    cache_dir: Path


def build_paths(
    settings: EduSettings,
    *,
    workspace: str | Path | None = None,
    skills_dir: str | Path | None = None,
) -> EduPaths:
    """Derive EduPaths from root settings and optional session overrides."""
    base = settings.agent.workspace
    root = _expand_path(workspace) if workspace not in (None, "") else _expand_path(base)

    if skills_dir not in (None, ""):
        sk = Path(skills_dir)
        skills = sk.expanduser().resolve() if sk.is_absolute() else (root / sk).resolve()
    else:
        rel = Path(settings.agent.skills_dir)
        skills = rel.expanduser().resolve() if rel.is_absolute() else (root / rel).resolve()

    return EduPaths(
        workspace=root,
        sessions_dir=root / "session_logs",
        profiles_dir=root / "learner_profiles",
        memory_dir=root / "memory",
        skills_dir=skills,
        logs_dir=root / "logs",
        cache_dir=root / "cache",
    )
