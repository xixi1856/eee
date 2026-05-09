"""SQLite SessionStore — structured Session / Message / ToolCall persistence."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from edu_agent.context.calculator import estimate_messages_tokens_rough
from edu_agent.sessions.models import (
    Message,
    Session,
    SessionMetadata,
    SessionStatus,
    new_id,
    openai_message_to_row_dicts,
    row_to_message,
    session_row_to_metadata,
    utcnow,
)
from edu_agent.sessions.schema import init_schema

logger = logging.getLogger(__name__)


class SessionStoreError(Exception):
    """Base error for session store operations."""


class SessionArchivedError(SessionStoreError):
    """Raised when mutating an archived session."""


class SessionNotFoundError(SessionStoreError):
    """Raised when session_id is missing."""


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        init_schema(self._conn)
        self._write_lock = threading.Lock()

    def _execute_write(self, fn):
        with self._write_lock:
            return fn()

    def _require_session_writable(self, session_id: str) -> None:
        """Must run inside _execute_write. Session must exist and not ARCHIVED."""
        row = self._conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        status = row["status"] if isinstance(row, sqlite3.Row) else row[0]
        if status == SessionStatus.ARCHIVED.value:
            raise SessionArchivedError(f"Session {session_id} is archived (read-only)")

    def create_session(self, user_id: str) -> Session:
        sid = new_id()
        now = utcnow().isoformat()
        meta = SessionMetadata(
            id=sid,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            created_at=utcnow(),
            updated_at=utcnow(),
            archived_at=None,
            title=None,
        )

        def _ins():
            self._conn.execute(
                """
                INSERT INTO sessions (id, user_id, status, created_at, updated_at, archived_at, title)
                VALUES (?, ?, ?, ?, ?, NULL, NULL)
                """,
                (sid, user_id, SessionStatus.ACTIVE.value, now, now),
            )
            self._conn.commit()

        self._execute_write(_ins)
        return Session(metadata=meta, messages=[])

    def get_or_create_session_by_id(self, session_id: str, user_id: str) -> Session:
        """Return an active session row for a stable id (e.g. ``wx_ilink_<ilink_user_id>``)."""
        existing = self.get_session(session_id)
        if existing is not None:
            if existing.metadata.user_id != user_id:
                logger.warning(
                    "session id=%s stored user_id=%s differs from inbound user_id=%s",
                    session_id,
                    existing.metadata.user_id,
                    user_id,
                )
            return existing
        now = utcnow().isoformat()
        meta = SessionMetadata(
            id=session_id,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            created_at=utcnow(),
            updated_at=utcnow(),
            archived_at=None,
            title=None,
        )

        def _ins():
            self._conn.execute(
                """
                INSERT INTO sessions (id, user_id, status, created_at, updated_at, archived_at, title)
                VALUES (?, ?, ?, ?, ?, NULL, NULL)
                """,
                (session_id, user_id, SessionStatus.ACTIVE.value, now, now),
            )
            self._conn.commit()

        self._execute_write(_ins)
        return Session(metadata=meta, messages=[])

    def get_session(self, session_id: str) -> Session | None:
        cur = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return Session(metadata=session_row_to_metadata(d), messages=[])

    def append_message(self, session_id: str, message: Message | dict[str, Any]) -> Message:
        token_count = 0
        if isinstance(message, dict):
            token_count = int(message.get("_token_count") or 0)
            if token_count <= 0:
                token_count = estimate_messages_tokens_rough([message])
            payload = {k: v for k, v in message.items() if k not in ("_token_count",)}
        else:
            token_count = message.metadata.token_count
            payload = message.to_openai_dict()

        def _append():
            self._require_session_writable(session_id)
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM messages WHERE session_id = ?",
                (session_id,),
            )
            next_seq = int(cur.fetchone()[0])
            row, tc_rows = openai_message_to_row_dicts(
                session_id,
                next_seq,
                payload,
                token_count=token_count,
            )
            self._conn.execute(
                """
                INSERT INTO messages (
                    id, session_id, seq, role, content_json, tool_calls_json, tool_call_id,
                    token_count, created_at, updated_at, is_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["session_id"],
                    row["seq"],
                    row["role"],
                    row["content_json"],
                    row["tool_calls_json"],
                    row["tool_call_id"],
                    row["token_count"],
                    row["created_at"],
                    row["updated_at"],
                    row["is_summary"],
                ),
            )
            for tc in tc_rows:
                self._conn.execute(
                    """
                    INSERT INTO tool_calls (id, message_id, tool_call_id, function_name, arguments)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tc["id"], tc["message_id"], tc["tool_call_id"], tc["function_name"], tc["arguments"]),
                )
            now = utcnow().isoformat()
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()
            return row["id"]

        mid = self._execute_write(_append)
        cur = self._conn.execute("SELECT * FROM messages WHERE id = ?", (mid,))
        return row_to_message(dict(cur.fetchone()))

    def update_message(self, session_id: str, message_id: str, updates: dict[str, Any]) -> None:
        sets: list[str] = []
        vals: list[Any] = []
        if "content" in updates:
            sets.append("content_json = ?")
            vals.append(json.dumps(updates["content"], ensure_ascii=False))
        if "token_count" in updates:
            sets.append("token_count = ?")
            vals.append(int(updates["token_count"]))
        if "tool_calls" in updates:
            tc = updates["tool_calls"]
            sets.append("tool_calls_json = ?")
            vals.append(json.dumps(tc, ensure_ascii=False) if tc else None)
        if "role" in updates:
            sets.append("role = ?")
            vals.append(updates["role"])
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(utcnow().isoformat())
        vals.extend([message_id, session_id])

        def _up():
            self._require_session_writable(session_id)
            self._conn.execute(
                f"UPDATE messages SET {', '.join(sets)} WHERE id = ? AND session_id = ?",
                vals,
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utcnow().isoformat(), session_id),
            )
            self._conn.commit()

        self._execute_write(_up)

    def list_messages(self, session_id: str, limit: int = 100, offset: int = 0) -> list[Message]:
        cur = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY seq ASC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        )
        return [row_to_message(dict(r)) for r in cur.fetchall()]

    def tail_messages(self, session_id: str, limit: int = 40) -> list[Message]:
        """Most recent messages first (seq descending). Read-only."""
        cur = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY seq DESC
            LIMIT ?
            """,
            (session_id, int(limit)),
        )
        return [row_to_message(dict(r)) for r in cur.fetchall()]

    def replace_session_messages(
        self,
        session_id: str,
        openai_messages: list[dict[str, Any]],
        token_counts: list[int] | None = None,
    ) -> None:
        """Replace all messages for a session (used after compression). Preserves session row."""

        def _repl():
            self._require_session_writable(session_id)
            try:
                self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                seq = 0
                for i, msg in enumerate(openai_messages):
                    tc = token_counts[i] if token_counts and i < len(token_counts) else 0
                    m = {k: v for k, v in msg.items() if k not in ("_token_count",)}
                    row, tc_rows = openai_message_to_row_dicts(session_id, seq, m, token_count=tc)
                    self._conn.execute(
                        """
                        INSERT INTO messages (
                            id, session_id, seq, role, content_json, tool_calls_json, tool_call_id,
                            token_count, created_at, updated_at, is_summary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["id"],
                            row["session_id"],
                            row["seq"],
                            row["role"],
                            row["content_json"],
                            row["tool_calls_json"],
                            row["tool_call_id"],
                            row["token_count"],
                            row["created_at"],
                            row["updated_at"],
                            row["is_summary"],
                        ),
                    )
                    for tc in tc_rows:
                        self._conn.execute(
                            """
                            INSERT INTO tool_calls (id, message_id, tool_call_id, function_name, arguments)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (tc["id"], tc["message_id"], tc["tool_call_id"], tc["function_name"], tc["arguments"]),
                        )
                    seq += 1
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (utcnow().isoformat(), session_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        self._execute_write(_repl)

    def update_session_status(self, session_id: str, status: SessionStatus) -> None:
        def _u():
            exists = self._conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if exists is None:
                raise SessionNotFoundError(session_id)
            now = utcnow().isoformat()
            if status == SessionStatus.ARCHIVED:
                self._conn.execute(
                    "UPDATE sessions SET status = ?, updated_at = ?, archived_at = ? WHERE id = ?",
                    (status.value, now, now, session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status.value, now, session_id),
                )
            self._conn.commit()

        self._execute_write(_u)

    def archive_session(self, session_id: str) -> None:
        self.update_session_status(session_id, SessionStatus.ARCHIVED)

    def search_sessions(
        self,
        user_id: str,
        keyword: str | None = None,
        status: SessionStatus | None = None,
        date_range: tuple[datetime, datetime] | None = None,
        limit: int = 20,
    ) -> list[Session]:
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if date_range is not None:
            clauses.append("created_at >= ? AND created_at <= ?")
            params.extend([date_range[0].isoformat(), date_range[1].isoformat()])
        where_sql = " AND ".join(clauses)
        kw_filter = ""
        if keyword:
            kw = f"%{keyword}%"
            kw_filter = """
            AND (
                title LIKE ?
                OR id IN (SELECT DISTINCT session_id FROM messages WHERE content_json LIKE ?)
            )
            """
            params.extend([kw, kw])

        sql = f"""
            SELECT * FROM sessions
            WHERE {where_sql} {kw_filter}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [Session(metadata=session_row_to_metadata(dict(r)), messages=[]) for r in cur.fetchall()]

    def search_sessions_by_tool_call(
        self,
        tool_name: str,
        date_range: tuple[datetime, datetime] | None = None,
        limit: int = 20,
    ) -> list[Session]:
        clauses = ["tc.function_name = ?"]
        params: list[Any] = [tool_name]
        if date_range:
            clauses.append("s.created_at >= ? AND s.created_at <= ?")
            params.extend([date_range[0].isoformat(), date_range[1].isoformat()])
        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT DISTINCT s.* FROM sessions s
            INNER JOIN messages m ON m.session_id = s.id
            INNER JOIN tool_calls tc ON tc.message_id = m.id
            WHERE {where_sql}
            ORDER BY s.updated_at DESC
            LIMIT ?
        """
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [Session(metadata=session_row_to_metadata(dict(r)), messages=[]) for r in cur.fetchall()]

    def delete_sessions_before(self, before: datetime, *, archived_only: bool = False) -> int:
        """Delete sessions (and messages via CASCADE) with created_at < before. Returns deleted count."""

        def _del():
            if archived_only:
                cur = self._conn.execute(
                    "DELETE FROM sessions WHERE created_at < ? AND status = ?",
                    (before.isoformat(), SessionStatus.ARCHIVED.value),
                )
            else:
                cur = self._conn.execute(
                    "DELETE FROM sessions WHERE created_at < ?",
                    (before.isoformat(),),
                )
            self._conn.commit()
            return cur.rowcount

        return int(self._execute_write(_del))

    def close(self) -> None:
        self._conn.close()
