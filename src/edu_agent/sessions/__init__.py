"""Structured SQLite session persistence.

Import ``SessionStore`` from ``edu_agent.sessions.store`` to avoid import cycles
(``store`` pulls in ``context`` which may reference sessions).
"""

from edu_agent.sessions.models import (
    Message,
    MessageMetadata,
    Session,
    SessionMetadata,
    SessionStatus,
    ToolCallRecord,
    openai_message_to_row_dicts,
    row_to_message,
    session_row_to_metadata,
)

__all__ = [
    "Message",
    "MessageMetadata",
    "Session",
    "SessionMetadata",
    "SessionStatus",
    "ToolCallRecord",
    "openai_message_to_row_dicts",
    "row_to_message",
    "session_row_to_metadata",
]
