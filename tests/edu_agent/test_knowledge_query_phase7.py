"""knowledge_query: session-bound course, strict sources/top_k (phase7 H5)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from edu_agent.tools.rag import _handle_knowledge_query


@pytest.mark.asyncio
async def test_missing_sources_rejected() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "c1"
    with patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx):
        out = await _handle_knowledge_query({"question": "q"})
    assert "sources" in out


@pytest.mark.asyncio
async def test_invalid_sources_rejected() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "c1"
    with patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx):
        out = await _handle_knowledge_query(
            {"question": "q", "sources": "bogus"},
        )
    assert "非法 sources" in out


@pytest.mark.asyncio
async def test_course_sources_without_runtime_course_rejected() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = None
    with patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx):
        out = await _handle_knowledge_query({"question": "q", "sources": "course"})
    assert "未绑定课程" in out


@pytest.mark.asyncio
async def test_invalid_top_k_rejected() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "550e8400-e29b-41d4-a716-446655440000"
    with patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx):
        out = await _handle_knowledge_query(
            {"question": "q", "sources": "personal", "top_k": "nope"},
        )
    assert "非法 top_k" in out


@pytest.mark.asyncio
async def test_top_k_out_of_range() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "550e8400-e29b-41d4-a716-446655440000"
    with patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx):
        out = await _handle_knowledge_query({"question": "q", "sources": "personal", "top_k": 99})
    assert "top_k" in out


@pytest.mark.asyncio
async def test_course_leg_uses_lightrag_retrieval_mock() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "550e8400-e29b-41d4-a716-446655440000"
    hits = [
        {
            "chunk_id": "c1",
            "text": "hello chunk",
            "metadata": {"material_id": "m1"},
            "relevance_score": 0.9,
        },
    ]
    with (
        patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx),
        patch(
            "edu_agent.tools.rag._sync_verify_and_query_course",
            return_value=hits,
        ),
    ):
        out = await _handle_knowledge_query(
            {"question": "q", "sources": "course", "mode": "hybrid", "top_k": 3},
        )
    assert "hello chunk" in out
    body = json.loads(out)
    assert any(
        h.get("material_id") == "m1" for h in body.get("payload", []) if isinstance(h, dict)
    )


@pytest.mark.asyncio
async def test_personal_leg_failure_does_not_drop_course_hits() -> None:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.course_id = "550e8400-e29b-41d4-a716-446655440000"
    course_hits = [
        {
            "chunk_id": "c1",
            "text": "course chunk",
            "metadata": {"material_id": None},
            "relevance_score": 0.9,
            "origin": "course",
        },
    ]
    with (
        patch("edu_agent.tools.rag.get_current_runtime", return_value=ctx),
        patch(
            "edu_agent.tools.rag._sync_verify_and_query_course",
            return_value=course_hits,
        ),
        patch(
            "rag_mvp.engine.personal_retrieval_hits_sync",
            side_effect=RuntimeError("personal down"),
        ),
    ):
        out = await _handle_knowledge_query(
            {"question": "q", "sources": "all", "mode": "hybrid", "top_k": 3},
        )
    body = json.loads(out)
    assert "course chunk" in out
    assert body.get("retrieval_warnings")
    assert any(h.get("origin") == "course" for h in body.get("payload", []))
