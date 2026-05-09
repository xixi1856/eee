"""Pydantic models for session persistence (OpenAI-compatible runtime mapping)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


OpenAIRole = Literal["user", "assistant", "system", "tool"]


class SessionMetadata(BaseModel):
    id: str
    user_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    title: str | None = None


class ToolCallRecord(BaseModel):
    """One function tool call (OpenAI tool_calls[]."""

    id: str
    type: Literal["function"] = "function"
    function_name: str
    arguments: str


class MessageMetadata(BaseModel):
    id: str
    session_id: str
    seq: int
    role: OpenAIRole
    created_at: datetime
    updated_at: datetime
    token_count: int = 0
    is_summary: bool = False


class Message(BaseModel):
    """Stored message with OpenAI-shaped fields."""

    metadata: MessageMetadata
    content: Any = None  # str | list | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    def to_openai_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.metadata.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.metadata.is_summary:
            d["_is_summary"] = True
        return d


class Session(BaseModel):
    metadata: SessionMetadata
    messages: list[Message] = Field(default_factory=list)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def openai_message_to_row_dicts(
    session_id: str,
    seq: int,
    message: dict[str, Any],
    *,
    message_id: str | None = None,
    token_count: int = 0,
    is_summary: bool | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build one messages-row dict and zero or more tool_calls rows from an OpenAI message."""
    mid = message_id or new_id()
    now = utcnow()
    clean = {k: v for k, v in message.items() if k not in ("_token_count", "_is_summary")}
    role = clean["role"]
    if is_summary is None:
        is_summary = bool(message.get("_is_summary", False))
    if role not in ("user", "assistant", "system", "tool"):
        raise ValueError(f"Unsupported message role: {role}")

    content = clean.get("content")
    tool_calls = clean.get("tool_calls")
    tool_call_id = clean.get("tool_call_id")

    row = {
        "id": mid,
        "session_id": session_id,
        "seq": seq,
        "role": role,
        "content_json": json.dumps(content, ensure_ascii=False) if content is not None else None,
        "tool_calls_json": json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
        "tool_call_id": tool_call_id,
        "token_count": token_count,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "is_summary": 1 if is_summary else 0,
    }

    tc_rows: list[dict[str, Any]] = []
    if tool_calls and role == "assistant":
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tid = tc.get("id")
            if not tid or not str(tid).strip():
                raise ValueError("tool_calls entry must include a non-empty id")
            fn = (tc.get("function") or {})
            tc_rows.append(
                {
                    "id": new_id(),
                    "message_id": mid,
                    "tool_call_id": str(tid),
                    "function_name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "") if isinstance(fn.get("arguments"), str) else json.dumps(
                        fn.get("arguments", ""), ensure_ascii=False
                    ),
                }
            )

    return row, tc_rows


def row_to_message(row: dict[str, Any]) -> Message:
    content_raw = row["content_json"]
    content: Any
    if content_raw is None:
        content = None
    else:
        content = json.loads(content_raw)

    tc_raw = row["tool_calls_json"]
    tool_calls = json.loads(tc_raw) if tc_raw else None

    meta = MessageMetadata(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        role=row["role"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        token_count=row["token_count"] or 0,
        is_summary=bool(row.get("is_summary", 0)),
    )
    return Message(
        metadata=meta,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=row.get("tool_call_id"),
    )


def session_row_to_metadata(row: dict[str, Any]) -> SessionMetadata:
    return SessionMetadata(
        id=row["id"],
        user_id=row["user_id"],
        status=SessionStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        archived_at=datetime.fromisoformat(row["archived_at"]) if row.get("archived_at") else None,
        title=row.get("title"),
    )
