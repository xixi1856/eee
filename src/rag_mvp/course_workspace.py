"""Stable course_id → LightRAG workspace mapping (Phase 7 decision 3)."""

from __future__ import annotations


def course_id_to_workspace(course_id: str) -> str:
    """Return the LightRAG workspace string for a course (1:1 with courses.id)."""
    cid = str(course_id).strip().lower()
    if not cid:
        raise ValueError("course_id must be non-empty")
    return f"course_{cid}"


def workspace_to_course_id(workspace: str) -> str:
    """Inverse of course_id_to_workspace for debugging/tests only."""
    w = str(workspace).strip()
    prefix = "course_"
    if not w.startswith(prefix):
        raise ValueError(f"not a course workspace: {workspace!r}")
    return w[len(prefix) :]
