"""Tests for SQLite SessionStore (structured sessions / messages / tool_calls)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from edu_agent.sessions.models import SessionStatus
from edu_agent.sessions.store import (
    SessionArchivedError,
    SessionNotFoundError,
    SessionStore,
)


@pytest.fixture()
def store(tmp_path) -> SessionStore:
    db = tmp_path / "sessions.db"
    s = SessionStore(db)
    yield s
    s.close()


class TestCreateAndMessages:
    def test_create_session_persists_row(self, store: SessionStore) -> None:
        s = store.create_session("alice")
        got = store.get_session(s.metadata.id)
        assert got is not None
        assert got.metadata.user_id == "alice"
        assert got.metadata.status == SessionStatus.ACTIVE

    def test_append_message_ordering(self, store: SessionStore) -> None:
        s = store.create_session("bob")
        sid = s.metadata.id
        store.append_message(sid, {"role": "user", "content": "hi"})
        store.append_message(sid, {"role": "assistant", "content": "yo"})
        rows = store.list_messages(sid)
        assert len(rows) == 2
        assert rows[0].metadata.seq == 0 and rows[0].metadata.role == "user"
        assert rows[1].metadata.seq == 1 and rows[1].metadata.role == "assistant"

    def test_append_tool_calls_rows(self, store: SessionStore) -> None:
        s = store.create_session("carol")
        sid = s.metadata.id
        store.append_message(
            sid,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "demo_tool", "arguments": "{}"},
                    }
                ],
            },
        )
        cur = store._conn.execute(  # noqa: SLF001 — test-only introspection
            "SELECT COUNT(*) FROM tool_calls tc JOIN messages m ON m.id = tc.message_id WHERE m.session_id = ?",
            (sid,),
        )
        assert cur.fetchone()[0] == 1

    def test_update_message_content(self, store: SessionStore) -> None:
        s = store.create_session("dave")
        sid = s.metadata.id
        m = store.append_message(sid, {"role": "assistant", "content": "old"})
        store.update_message(sid, m.metadata.id, {"content": "new"})
        rows = store.list_messages(sid)
        assert rows[0].content == "new"

    def test_archive_blocks_append(self, store: SessionStore) -> None:
        s = store.create_session("eve")
        sid = s.metadata.id
        store.archive_session(sid)
        with pytest.raises(SessionArchivedError):
            store.append_message(sid, {"role": "user", "content": "x"})

    def test_unknown_session_append(self, store: SessionStore) -> None:
        with pytest.raises(SessionNotFoundError):
            store.append_message("nonexistent-id", {"role": "user", "content": "a"})

    def test_update_session_status_unknown_raises(self, store: SessionStore) -> None:
        with pytest.raises(SessionNotFoundError):
            store.update_session_status("00000000-0000-0000-0000-000000000000", SessionStatus.IDLE)


class TestSearch:
    def test_search_sessions_by_user(self, store: SessionStore) -> None:
        a = store.create_session("u1")
        b = store.create_session("u1")
        _ = store.create_session("u2")
        out = store.search_sessions("u1", limit=10)
        ids = {x.metadata.id for x in out}
        assert a.metadata.id in ids and b.metadata.id in ids
        assert all(x.metadata.user_id == "u1" for x in out)

    def test_search_keyword_on_title(self, store: SessionStore) -> None:
        s = store.create_session("kw")
        store._conn.execute(  # noqa: SLF001
            "UPDATE sessions SET title = ? WHERE id = ?",
            ("math homework", s.metadata.id),
        )
        store._conn.commit()
        out = store.search_sessions("kw", keyword="homework")
        assert len(out) >= 1
        assert out[0].metadata.id == s.metadata.id


class TestConcurrentAppend:
    def test_concurrent_appends_same_session(self, store: SessionStore) -> None:
        s = store.create_session("thread")
        sid = s.metadata.id
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for _ in range(5):
                    store.append_message(sid, {"role": "user", "content": f"msg-{n}"})
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        rows = store.list_messages(sid, limit=500)
        assert len(rows) == 20


class TestConcurrentArchiveVsAppend:
    def test_append_stops_after_archive_under_race(self, store: SessionStore) -> None:
        """Archived session must not accept appends even if another thread raced archive."""
        s = store.create_session("race")
        sid = s.metadata.id
        archived_hit = threading.Event()

        def spam_append() -> None:
            for _ in range(50_000):
                try:
                    store.append_message(sid, {"role": "user", "content": "x"})
                except SessionArchivedError:
                    archived_hit.set()
                    return

        t = threading.Thread(target=spam_append, daemon=True)
        t.start()
        time.sleep(0.02)
        store.archive_session(sid)
        t.join(timeout=10)
        assert archived_hit.is_set(), "append thread should observe ARCHIVED after archive_session"
        with pytest.raises(SessionArchivedError):
            store.append_message(sid, {"role": "user", "content": "after"})


class TestReplaceMessages:
    def test_replace_session_messages_resets_seq(self, store: SessionStore) -> None:
        s = store.create_session("rep")
        sid = s.metadata.id
        store.append_message(sid, {"role": "user", "content": "a"})
        store.replace_session_messages(
            sid,
            [{"role": "user", "content": "only"}],
            token_counts=[1],
        )
        rows = store.list_messages(sid)
        assert len(rows) == 1
        assert rows[0].metadata.seq == 0

    def test_replace_session_messages_rollback_on_invalid_row(self, store: SessionStore) -> None:
        s = store.create_session("rb")
        sid = s.metadata.id
        store.append_message(sid, {"role": "user", "content": "keep"})
        with pytest.raises(ValueError, match="Unsupported message role"):
            store.replace_session_messages(
                sid,
                [{"role": "invalid_role", "content": "nope"}],
                token_counts=[1],
            )
        rows = store.list_messages(sid)
        assert len(rows) == 1
        assert rows[0].content == "keep"


class TestCleanup:
    def test_delete_sessions_before(self, store: SessionStore) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=400)
        s = store.create_session("olduser")
        store._conn.execute(  # noqa: SLF001
            "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
            (old.isoformat(), old.isoformat(), s.metadata.id),
        )
        store._conn.commit()
        n = store.delete_sessions_before(datetime.now(timezone.utc), archived_only=False)
        assert n >= 1
        assert store.get_session(s.metadata.id) is None
