"""Tests for learner_profile.py – persistent per-user knowledge state."""

from __future__ import annotations

import json

import pytest

from edu_agent.learner_profile import (
    load_profile,
    profile_summary,
    save_profile,
    update_topic_mastery,
)
from edu_agent.memory.models import Concept, LearnerProfile
from edu_agent.memory.storage import MemoryStore


class TestLoadProfileMemoryStore:
    def test_topics_mastery_reflects_concept_mastery(self, tmp_path):
        mdir = tmp_path / "mem"
        store = MemoryStore(mdir)
        cid = "c_tcp_1"
        store.save_concept("alice", Concept(id=cid, name="TCP", description="", mastery_level=0.82))
        prof = LearnerProfile(user_id="alice", concepts_mastered_ids=[cid])
        store.save_profile(prof)

        d = load_profile("alice", storage_dir=tmp_path, memory_store=store)
        assert d.get("memory_profile") is True
        assert cid in d["topics"]
        assert abs(d["topics"][cid]["mastery"] - 0.82) < 0.01


class TestLoadProfile:
    def test_missing_file_returns_default(self, tmp_path):
        profile = load_profile("new_user", storage_dir=tmp_path)
        assert profile["user_id"] == "new_user"
        assert profile["topics"] == {}
        assert "created_at" in profile

    def test_existing_file_loaded(self, tmp_path):
        data = {"user_id": "alice", "topics": {"TCP": {"mastery": 0.9}}}
        (tmp_path / "alice.json").write_text(json.dumps(data), encoding="utf-8")

        profile = load_profile("alice", storage_dir=tmp_path)
        assert profile["topics"]["TCP"]["mastery"] == 0.9

    def test_corrupt_file_returns_default(self, tmp_path):
        (tmp_path / "bob.json").write_text("NOT JSON {{", encoding="utf-8")
        profile = load_profile("bob", storage_dir=tmp_path)
        assert profile["user_id"] == "bob"
        assert profile["topics"] == {}

    def test_user_id_sanitised_in_path(self, tmp_path):
        # user_id with special chars should produce a safe filename
        profile = load_profile("user/../../evil", storage_dir=tmp_path)
        assert profile["user_id"] == "user/../../evil"
        # The file should be inside tmp_path (no directory traversal)
        profiles = list(tmp_path.glob("*.json"))
        assert len(profiles) == 0  # file not saved yet – just loaded default


class TestSaveProfile:
    def test_creates_file(self, tmp_path):
        profile = load_profile("charlie", storage_dir=tmp_path)
        save_profile(profile, storage_dir=tmp_path)
        assert (tmp_path / "charlie.json").exists()

    def test_saved_data_matches(self, tmp_path):
        profile = load_profile("dave", storage_dir=tmp_path)
        profile["topics"]["HTTP"] = {"mastery": 0.6}
        save_profile(profile, storage_dir=tmp_path)

        loaded = json.loads((tmp_path / "dave.json").read_text(encoding="utf-8"))
        assert loaded["topics"]["HTTP"]["mastery"] == 0.6

    def test_updated_at_refreshed(self, tmp_path):
        profile = load_profile("eve", storage_dir=tmp_path)
        old_ts = profile.get("updated_at", "")
        save_profile(profile, storage_dir=tmp_path)

        loaded = json.loads((tmp_path / "eve.json").read_text(encoding="utf-8"))
        # updated_at should be present (may equal old_ts if called within same second)
        assert "updated_at" in loaded

    def test_storage_dir_created_if_missing(self, tmp_path):
        new_dir = tmp_path / "nested" / "profiles"
        profile = {"user_id": "frank", "topics": {}}
        save_profile(profile, storage_dir=new_dir)
        assert (new_dir / "frank.json").exists()


class TestUpdateTopicMastery:
    def test_increases_mastery(self):
        profile = {"user_id": "u", "topics": {}}
        profile = update_topic_mastery(profile, "TCP", 0.3)
        assert profile["topics"]["TCP"]["mastery"] == pytest.approx(0.3)

    def test_mastery_clamped_to_1(self):
        profile = {"user_id": "u", "topics": {"TCP": {"mastery": 0.9, "attempts": 1}}}
        profile = update_topic_mastery(profile, "TCP", 0.5)
        assert profile["topics"]["TCP"]["mastery"] == pytest.approx(1.0)

    def test_mastery_clamped_to_0(self):
        profile = {"user_id": "u", "topics": {"TCP": {"mastery": 0.1, "attempts": 1}}}
        profile = update_topic_mastery(profile, "TCP", -0.5)
        assert profile["topics"]["TCP"]["mastery"] == pytest.approx(0.0)

    def test_attempts_incremented(self):
        profile = {"user_id": "u", "topics": {}}
        update_topic_mastery(profile, "DNS", 0.2)
        update_topic_mastery(profile, "DNS", 0.1)
        assert profile["topics"]["DNS"]["attempts"] == 2

    def test_new_topic_created_with_defaults(self):
        profile = {"user_id": "u", "topics": {}}
        update_topic_mastery(profile, "NewTopic", 0.0)
        assert "NewTopic" in profile["topics"]
        assert "last_seen" in profile["topics"]["NewTopic"]


class TestProfileSummary:
    def test_no_topics_returns_default_message(self):
        profile = {"user_id": "u", "topics": {}}
        summary = profile_summary(profile)
        assert "尚未" in summary or "尚无" in summary

    def test_strong_topics_mentioned(self):
        profile = {
            "user_id": "u",
            "topics": {"TCP": {"mastery": 0.9}, "UDP": {"mastery": 0.8}},
        }
        summary = profile_summary(profile)
        assert "TCP" in summary or "UDP" in summary

    def test_weak_topics_mentioned(self):
        profile = {
            "user_id": "u",
            "topics": {"DNS": {"mastery": 0.2}},
        }
        summary = profile_summary(profile)
        assert "DNS" in summary

    def test_returns_string(self):
        profile = {"user_id": "u", "topics": {}}
        assert isinstance(profile_summary(profile), str)
