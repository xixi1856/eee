"""A5 message bus: immutable inbound and typed outbound streaming messages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from edu_agent.sessions.models import utcnow


class ChannelKind(str, Enum):
    """Transport / entry channel (not business domain)."""

    CLI = "cli"
    HTTP = "http"
    WEBSOCKET = "websocket"
    WEIXIN = "weixin"


class InboundKind(str, Enum):
    """Inbound message semantics."""

    USER_TEXT = "user_text"
    INTERRUPT = "interrupt"
    CANCEL = "cancel"


class OutboundContentType(str, Enum):
    """Outbound payload category (no bare string content_type)."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    META = "meta"


def new_message_id() -> UUID:
    return uuid4()


def ensure_aware_utc(dt: datetime | None = None) -> datetime:
    """Timezone-aware UTC timestamp for bus messages."""
    if dt is None:
        return utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class InboundMessage(BaseModel):
    """Immutable inbound envelope (transport + routing)."""

    model_config = ConfigDict(frozen=True)

    message_id: UUID = Field(default_factory=new_message_id)
    channel: ChannelKind
    session_id: str
    user_id: str
    timestamp: datetime = Field(default_factory=ensure_aware_utc)
    kind: InboundKind = InboundKind.USER_TEXT
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def user_text(
        cls,
        *,
        channel: ChannelKind,
        session_id: str,
        user_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        message_id: UUID | None = None,
        timestamp: datetime | None = None,
    ) -> InboundMessage:
        return cls(
            message_id=message_id or new_message_id(),
            channel=channel,
            session_id=session_id,
            user_id=user_id,
            timestamp=ensure_aware_utc(timestamp),
            kind=InboundKind.USER_TEXT,
            content=content,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def control(
        cls,
        *,
        kind: InboundKind,
        channel: ChannelKind,
        session_id: str,
        user_id: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        message_id: UUID | None = None,
        timestamp: datetime | None = None,
    ) -> InboundMessage:
        if kind not in (InboundKind.INTERRUPT, InboundKind.CANCEL):
            raise ValueError(f"Not a control kind: {kind}")
        return cls(
            message_id=message_id or new_message_id(),
            channel=channel,
            session_id=session_id,
            user_id=user_id,
            timestamp=ensure_aware_utc(timestamp),
            kind=kind,
            content=content,
            metadata=dict(metadata or {}),
        )


class OutboundMessage(BaseModel):
    """Streaming-friendly outbound chunk (one logical event per instance)."""

    model_config = ConfigDict(frozen=True)

    message_id: UUID = Field(default_factory=new_message_id)
    in_reply_to: UUID
    session_id: str
    user_id: str
    timestamp: datetime = Field(default_factory=ensure_aware_utc)
    content: str = ""
    content_type: OutboundContentType = OutboundContentType.TEXT
    is_final: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
