"""Tests for session_store.py – append-only JSONL session transcripts."""

from __future__ import annotations

import json

import pytest

from edu_agent.session_store import append_turn, list_sessions, load_session


class TestAppendTurn:
    def test_creates_jsonl_file(self, tmp_path):
        append_turn("sess1", "alice", "user", "你好", storage_dir=tmp_path)
        assert (tmp_path / "sess1.jsonl").exists()

    def test_each_turn_is_valid_json_line(self, tmp_path):
        append_turn("sess2", "bob", "user", "问题", storage_dir=tmp_path)
        append_turn("sess2", "bob", "assistant", "回答", storage_dir=tmp_path)

        lines = (tmp_path / "sess2.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert "ts" in record
            assert "role" in record
            assert "content" in record

    def test_content_preserved(self, tmp_path):
        append_turn("sess3", "carol", "user", "测试内容", storage_dir=tmp_path)
        lines = (tmp_path / "sess3.jsonl").read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        assert record["content"] == "测试内容"
        assert record["role"] == "user"
        assert record["user_id"] == "carol"

    def test_storage_dir_created_if_missing(self, tmp_path):
        new_dir = tmp_path / "deep" / "logs"
        append_turn("sess4", "dave", "assistant", "ok", storage_dir=new_dir)
        assert (new_dir / "sess4.jsonl").exists()

    def test_append_is_incremental(self, tmp_path):
        for i in range(5):
            append_turn("sess5", "eve", "user", f"turn {i}", storage_dir=tmp_path)
        lines = (tmp_path / "sess5.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5


class TestLoadSession:
    def test_missing_session_returns_empty_list(self, tmp_path):
        turns = load_session("nonexistent", storage_dir=tmp_path)
        assert turns == []

    def test_loaded_turns_match_appended(self, tmp_path):
        append_turn("s1", "frank", "user", "first", storage_dir=tmp_path)
        append_turn("s1", "frank", "assistant", "reply", storage_dir=tmp_path)

        turns = load_session("s1", storage_dir=tmp_path)
        assert len(turns) == 2
        assert turns[0]["content"] == "first"
        assert turns[1]["content"] == "reply"

    def test_skips_malformed_lines(self, tmp_path):
        jsonl_path = tmp_path / "bad.jsonl"
        jsonl_path.write_text(
            '{"role": "user", "content": "ok"}\nNOT JSON\n{"role": "assistant", "content": "fine"}\n',
            encoding="utf-8",
        )
        turns = load_session("bad", storage_dir=tmp_path)
        assert len(turns) == 2  # malformed line skipped

    def test_returns_list_of_dicts(self, tmp_path):
        append_turn("s2", "grace", "user", "hi", storage_dir=tmp_path)
        turns = load_session("s2", storage_dir=tmp_path)
        assert isinstance(turns, list)
        assert isinstance(turns[0], dict)


class TestListSessions:
    def test_missing_dir_returns_empty(self, tmp_path):
        sessions = list_sessions(storage_dir=tmp_path / "no_such_dir")
        assert sessions == []

    def test_lists_all_session_ids(self, tmp_path):
        for sid in ["alpha", "beta", "gamma"]:
            append_turn(sid, "user", "user", "msg", storage_dir=tmp_path)

        sessions = list_sessions(storage_dir=tmp_path)
        assert set(sessions) == {"alpha", "beta", "gamma"}

    def test_sessions_sorted(self, tmp_path):
        for sid in ["zzz", "aaa", "mmm"]:
            append_turn(sid, "user", "user", "msg", storage_dir=tmp_path)

        sessions = list_sessions(storage_dir=tmp_path)
        assert sessions == sorted(sessions)
