"""MemoryRetriever deterministic ranking."""

from __future__ import annotations

from edu_agent.memory.models import Concept
from edu_agent.memory.retriever import MemoryRetriever
from edu_agent.memory.storage import MemoryStore


def test_retriever_ordering_stable(tmp_path):
    store = MemoryStore(tmp_path / "m")
    for name, desc in [
        ("algebra basics", "linear equations introduction"),
        ("TCP congestion", "slow start and AIMD"),
        ("cooking pasta", "boiling water"),
    ]:
        store.save_concept(
            "u",
            Concept(name=name, description=desc, mastery_level=0.5),
        )
    ret = MemoryRetriever(store)
    ctx = {"topic": "network transport", "keywords": "TCP congestion control"}
    out = ret.get_relevant_concepts("u", ctx, max_results=5)
    assert out
    # TCP concept should rank above unrelated topics for this query
    assert "TCP" in out[0].name or "TCP" in out[0].description


def test_search_concepts_keyword(tmp_path):
    store = MemoryStore(tmp_path / "m")
    store.save_concept("u", Concept(name="Quadratic equations", description="parabolas", mastery_level=0.4))
    ret = MemoryRetriever(store)
    out = ret.search_concepts("u", "quadratic", limit=5)
    assert len(out) == 1
