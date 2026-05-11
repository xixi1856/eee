"""course_id ↔ workspace mapping."""

from __future__ import annotations

import pytest

from rag_mvp.course_workspace import course_id_to_workspace, workspace_to_course_id


def test_course_id_to_workspace_stable() -> None:
    cid = "550E8400-E29B-41D4-A716-446655440000"
    assert course_id_to_workspace(cid) == "course_550e8400-e29b-41d4-a716-446655440000"


def test_workspace_roundtrip() -> None:
    cid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    w = course_id_to_workspace(cid)
    assert workspace_to_course_id(w) == cid.lower()


def test_workspace_invalid() -> None:
    with pytest.raises(ValueError):
        workspace_to_course_id("personal_default")
