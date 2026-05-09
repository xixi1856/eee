"""MemoryConsolidator aggregation semantics (no live LLM)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from edu_agent.config import EduSettings
from edu_agent.memory.consolidator import MemoryConsolidator
from edu_agent.memory.models import Fact, FactSource, MemoryConfig
from edu_agent.memory.storage import MemoryStore


def _fact(
    uid: str,
    sid: str,
    *,
    cat: str,
    content: str,
    conf: float,
    mid: str,
    ts: datetime | None = None,
) -> Fact:
    ts = ts or datetime.now(timezone.utc)
    return Fact(
        user_id=uid,
        session_id=sid,
        timestamp=ts,
        category=cat,  # type: ignore[arg-type]
        content=content,
        confidence=conf,
        source=FactSource(session_id=sid, message_id=mid),
    )


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(tmp_path / "mem")


def test_aggregate_updates_profile_evolution(store, minimal_edu_settings: EduSettings):
    now = datetime.now(timezone.utc)
    store.add_fact(
        _fact(
            "u",
            "s",
            cat="concept_mastery",
            content="understands TCP",
            conf=0.9,
            mid="1",
            ts=now - timedelta(days=1),
        )
    )
    store.add_fact(
        _fact(
            "u",
            "s",
            cat="concept_mastery",
            content="understands TCP",
            conf=0.8,
            mid="2",
            ts=now,
        )
    )
    ext = MagicMock()
    ext.extract_facts_from_session.return_value = []
    cons = MemoryConsolidator(store, ext, minimal_edu_settings, MemoryConfig(consolidation_days_lookback=7))
    cons.aggregate_facts_to_concepts("u", days_lookback=7)
    prof = cons.aggregate_concepts_to_profile("u")
    assert prof.user_id == "u"
    assert prof.snapshots, "snapshot should be appended"
    assert prof.concepts_mastered_ids or prof.recent_topics


def test_consolidate_skips_llm_when_extract_after_seq_covers_all_messages(
    store, minimal_edu_settings: EduSettings
):
    from edu_agent.sessions.models import Message, MessageMetadata

    now = datetime.now(timezone.utc)
    m = Message(
        metadata=MessageMetadata(
            id="mid-1",
            session_id="sid",
            seq=1,
            role="user",
            created_at=now,
            updated_at=now,
        ),
        content="hi",
    )
    ext = MagicMock()
    ext.extract_facts_from_session.return_value = []
    cons = MemoryConsolidator(store, ext, minimal_edu_settings, MemoryConfig())
    cons.consolidate_session("u", "sid", [m], force_extract=False, extract_after_seq=1)
    ext.extract_facts_from_session.assert_not_called()


def test_detect_and_resolve_deprecates_weaker_conflicting_fact(
    store, minimal_edu_settings: EduSettings
):
    now = datetime.now(timezone.utc)
    old = _fact(
        "u",
        "s",
        cat="concept_confusion",
        content="struggles with TCP retransmission",
        conf=0.6,
        mid="a",
        ts=now - timedelta(days=2),
    )
    store.add_fact(old)
    new = _fact(
        "u",
        "s",
        cat="concept_mastery",
        content="Struggles with TCP retransmission",
        conf=0.9,
        mid="b",
        ts=now,
    )
    ext = MagicMock()
    cons = MemoryConsolidator(store, ext, minimal_edu_settings, MemoryConfig())
    cons.detect_and_resolve_conflicts("u", new)
    assert not new.metadata.get("deprecated")
    assert old.id in new.metadata.get("deprecates_fact_ids", [])


def test_detect_and_resolve_deprecates_new_when_old_wins(
    store, minimal_edu_settings: EduSettings
):
    now = datetime.now(timezone.utc)
    old = _fact(
        "u",
        "s",
        cat="concept_mastery",
        content="masters quadratic equations",
        conf=0.95,
        mid="a",
        ts=now,
    )
    store.add_fact(old)
    new = _fact(
        "u",
        "s",
        cat="concept_confusion",
        content="Masters quadratic equations",
        conf=0.3,
        mid="b",
        ts=now - timedelta(hours=1),
    )
    ext = MagicMock()
    cons = MemoryConsolidator(store, ext, minimal_edu_settings, MemoryConfig())
    cons.detect_and_resolve_conflicts("u", new)
    assert new.metadata.get("deprecated")


def test_concept_retains_supporting_fact_ids(store, minimal_edu_settings: EduSettings):
    f1 = _fact("u", "s", cat="concept_mastery", content="learned UDP ports", conf=0.7, mid="a")
    f2 = _fact("u", "s", cat="concept_mastery", content="learned UDP ports", conf=0.75, mid="b")
    store.add_fact(f1)
    store.add_fact(f2)
    ext = MagicMock()
    ext.extract_facts_from_session.return_value = []
    cons = MemoryConsolidator(store, ext, minimal_edu_settings, MemoryConfig())
    cons.aggregate_facts_to_concepts("u", days_lookback=7)
    concepts = store.list_concepts("u")
    assert concepts
    c0 = concepts[0]
    assert set(c0.supporting_fact_ids) == {f1.id, f2.id}
