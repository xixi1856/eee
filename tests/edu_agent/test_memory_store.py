"""MemoryStore append-only facts and concept/profile persistence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from edu_agent.memory.models import Concept, Fact, FactSource, LearnerProfile
from edu_agent.memory.storage import MemoryStore


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(tmp_path / "memory")


def test_add_fact_append_only(store):
    f1 = Fact(
        user_id="u1",
        session_id="s1",
        category="preference",
        content="User prefers short explanations",
        confidence=0.9,
        source=FactSource(session_id="s1", message_id="m1"),
    )
    f2 = f1.model_copy(update={"id": "otherid", "content": "Second fact", "source": FactSource(session_id="s1", message_id="m2")})
    store.add_fact(f1)
    store.add_fact(f2)
    all_rows = store._load_all_facts("u1")
    assert len(all_rows) == 2
    assert {x.id for x in all_rows} == {f1.id, f2.id}


def test_fact_provenance_roundtrip(store):
    f = Fact(
        user_id="u1",
        session_id="sess-a",
        category="question",
        content="Asked about TCP handshake",
        confidence=0.7,
        source=FactSource(session_id="sess-a", message_id="mid-99", tool_call_id="tc1", tool_name="x"),
    )
    store.add_fact(f)
    loaded = store.get_facts("u1")
    assert len(loaded) == 1
    assert loaded[0].source.message_id == "mid-99"
    assert loaded[0].source.session_id == "sess-a"


def test_search_facts_keyword(store):
    store.add_fact(
        Fact(
            user_id="u1",
            session_id="s",
            category="achievement",
            content="Completed quadratic exercise set",
            confidence=1.0,
            source=FactSource(session_id="s", message_id="a"),
        )
    )
    hits = store.search_facts("u1", "quadratic")
    assert len(hits) == 1


def test_save_concept_and_search(store):
    c = Concept(id="cid1", name="TCP flow control", description="Window-based", mastery_level=0.6)
    store.save_concept("u1", c)
    got = store.get_concept("u1", "cid1")
    assert got is not None
    assert got.name == "TCP flow control"
    found = store.search_concepts("u1", "TCP")
    assert any(x.id == "cid1" for x in found)


def test_profile_save_load(store):
    p = LearnerProfile(user_id="u1", name="Alice")
    store.save_profile(p)
    out = store.load_profile("u1")
    assert out is not None
    assert out.name == "Alice"


def test_deprecated_fact_excluded_via_deprecates_fact_ids_list(store):
    f = Fact(
        user_id="u1",
        session_id="s",
        category="preference",
        content="Old preference",
        confidence=0.5,
        source=FactSource(session_id="s", message_id="m1"),
    )
    store.add_fact(f)
    g = Fact(
        user_id="u1",
        session_id="s",
        category="preference",
        content="Correction",
        confidence=0.9,
        source=FactSource(session_id="s", message_id="m2"),
        metadata={"deprecates_fact_ids": [f.id]},
    )
    store.add_fact(g)
    active = store.get_facts("u1")
    assert len(active) == 1
    assert active[0].id == g.id


def test_deprecated_fact_excluded_from_get_facts(store):
    f = Fact(
        user_id="u1",
        session_id="s",
        category="preference",
        content="Old preference",
        confidence=0.5,
        source=FactSource(session_id="s", message_id="m1"),
    )
    store.add_fact(f)
    g = Fact(
        user_id="u1",
        session_id="s",
        category="preference",
        content="Correction",
        confidence=0.9,
        source=FactSource(session_id="s", message_id="m2"),
        metadata={"deprecates_fact_id": f.id},
    )
    store.add_fact(g)
    active = store.get_facts("u1")
    assert len(active) == 1
    assert active[0].id == g.id
