"""Shared fake agent for A5 runner/gateway tests (no LLM)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from edu_agent.bus.models import OutboundContentType, OutboundMessage, new_message_id
from edu_agent.types import AgentConfig


class FakeEduAgent:
    """Minimal stand-in for ``EduAgent`` inside ``SessionRunner``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.config = kwargs.get("config") or AgentConfig()
        self.callbacks = None
        self._delay = 0.0
        self._turn_calls = 0

    async def run_turn_stream(
        self,
        user_input: str,
        *,
        in_reply_to: UUID,
    ) -> AsyncIterator[OutboundMessage]:
        self._turn_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        for ch in user_input[:3]:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=in_reply_to,
                session_id=self.config.session_id,
                user_id=self.config.user_id,
                content=ch,
                content_type=OutboundContentType.TEXT,
                is_final=False,
            )
        yield OutboundMessage(
            message_id=new_message_id(),
            in_reply_to=in_reply_to,
            session_id=self.config.session_id,
            user_id=self.config.user_id,
            content=user_input,
            content_type=OutboundContentType.TEXT,
            is_final=True,
        )

    def trigger_context_compress(self) -> None:
        return

    def finalize_memory_session(self) -> None:
        return
