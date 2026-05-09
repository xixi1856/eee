"""SQLite schema for EduAgent sessions (structured, index-friendly)."""

from __future__ import annotations

SCHEMA_VERSION = 1

INIT_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    title TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON sessions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status_updated
    ON sessions (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_summary INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_session_role ON messages (session_id, role);
CREATE INDEX IF NOT EXISTS idx_messages_tool_call_id ON messages (tool_call_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tool_call_id TEXT NOT NULL,
    function_name TEXT NOT NULL,
    arguments TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_function ON tool_calls (function_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_call_id ON tool_calls (tool_call_id);
"""


def init_schema(conn) -> None:
    """Apply DDL and record schema version (idempotent)."""
    conn.executescript(INIT_SQL)
    cur = conn.execute("SELECT version FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,))
    if cur.fetchone() is None:
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
