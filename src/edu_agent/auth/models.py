"""Auth models (Gateway boundary only)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Permission(str, Enum):
    READ_SESSION = "read_session"
    WRITE_SESSION = "write_session"
    LIST_TOOLS = "list_tools"
    CREATE_SESSION = "create_session"


class AuthContext(BaseModel):
    """Resolved caller identity for one inbound message."""

    user_id: str
    channel: str
    api_key: str | None = None
    token: str | None = None
    permissions: list[Permission] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
