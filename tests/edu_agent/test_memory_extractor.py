"""MemoryExtractor JSON parsing and provenance filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from edu_agent.config import EduSettings
from edu_agent.memory.extractor import MemoryExtractor
from edu_agent.memory.models import FactSource
from edu_agent.providers.types import ResolvedProviderRuntime
from edu_agent.sessions.models import Message, MessageMetadata


def _msg(mid: str, role: str, content: str, seq: int) -> Message:
    now = datetime.now(timezone.utc)
    return Message(
        metadata=MessageMetadata(
            id=mid,
            session_id="sid",
            seq=seq,
            role=role,  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        ),
        content=content,
    )


@pytest.fixture()
def extractor(minimal_edu_settings: EduSettings):
    rt = ResolvedProviderRuntime(
        provider_id="dashscope",
        model="m",
        base_url="https://example.com",
        api_key="k",
        api_mode="chat_completions",
        client_kind="openai",
        temperature=0.1,
        max_tokens=100,
    )
    ext = MemoryExtractor(rt, minimal_edu_settings)
    ext._client = MagicMock()
    return ext


def test_extractor_drops_invalid_message_id(extractor):
    good = _msg("valid-1", "user", "I dislike memorizing formulas", 1)
    extractor._client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='[{"category":"preference","confidence":0.8,"content":"Dislikes memorizing","source_message_id":"bad-id"}]'))]
    )
    facts = extractor.extract_facts_from_session("u", "sid", [good])
    assert facts == []


def test_extractor_accepts_valid_rows(extractor):
    m1 = _msg("m-1", "user", "I mastered TCP retransmission", 1)
    extractor._client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content="""[{"category":"concept_mastery","confidence":0.85,"content":"Says they mastered TCP retransmission","source_message_id":"m-1"}]"""
                )
            )
        ]
    )
    facts = extractor.extract_facts_from_session("alice", "sid", [m1])
    assert len(facts) == 1
    assert facts[0].source.message_id == "m-1"
    assert facts[0].session_id == "sid"
    assert facts[0].user_id == "alice"


def test_extractor_malformed_json_returns_empty(extractor):
    extractor._client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="not-json"))]
    )
    facts = extractor.extract_facts_from_session("u", "sid", [_msg("x", "user", "hi", 1)])
    assert facts == []
