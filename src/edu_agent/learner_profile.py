"""Learner profile: persistent per-user knowledge state stored as JSON.

Each user has a single profile file at ``<storage_dir>/<user_id>.json``.

Schema (all fields optional / have defaults)::

    {
      "user_id": "alice",
      "created_at": "2025-05-06T00:00:00",
      "updated_at": "2025-05-06T01:00:00",
      "topics": {
        "TCP": {"mastery": 0.8, "attempts": 3, "last_seen": "2025-05-06"},
        "UDP": {"mastery": 0.5, "attempts": 1, "last_seen": "2025-05-06"}
      },
      "preferences": {
        "question_type": "mixed",
        "difficulty": "medium"
      }
    }

Mastery values are floats in [0.0, 1.0].  They are updated externally by
the agent/quiz logic; this module only handles persistence.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edu_agent.memory.models import LearnerProfile

logger = logging.getLogger(__name__)

_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def _profile_path(user_id: str, storage_dir: Path) -> Path:
    # Sanitise user_id to a safe filename component.
    safe = re.sub(r"[^\w\-.]", "_", user_id)[:64]
    return storage_dir / f"{safe}.json"


import re  # noqa: E402 – placed after _profile_path to keep the docstring clean


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _learner_profile_to_dict(
    lp: LearnerProfile,
    *,
    concepts_store: Any | None = None,
) -> dict[str, Any]:
    """Map MemoryStore LearnerProfile → legacy dict shape for ``profile_summary``."""
    mastery_by_id: dict[str, float] = {}
    if concepts_store is not None:
        try:
            for c in concepts_store.list_concepts(lp.user_id):
                mastery_by_id[c.id] = float(c.mastery_level)
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_concepts failed for profile summary: %s", exc)

    topics: dict[str, Any] = {}
    for tid in lp.concepts_mastered_ids[:12]:
        label = tid if len(tid) <= 24 else tid[-24:]
        m = float(mastery_by_id.get(tid, 0.7))
        topics[label] = {
            "mastery": max(0.0, min(1.0, m)),
            "attempts": 1,
            "last_seen": lp.updated_at.date().isoformat() if lp.updated_at else _now_iso()[:10],
        }
    for tid in lp.concepts_struggling_ids[:10]:
        label = tid if len(tid) <= 24 else tid[-24:]
        m = float(mastery_by_id.get(tid, 0.35))
        prev = topics.get(label, {"mastery": m, "attempts": 0, "last_seen": ""})
        prev["mastery"] = min(float(prev.get("mastery", m)), max(0.0, min(1.0, m)))
        prev["attempts"] = int(prev.get("attempts", 0)) + 1
        topics[label] = prev
    prefs: dict[str, str] = {}
    if lp.learning_style:
        prefs["learning_style"] = str(lp.learning_style)
    if lp.pace_preference:
        prefs["pace"] = str(lp.pace_preference)
    created = lp.created_at.strftime(_ISO_FMT) if lp.created_at else _now_iso()
    updated = lp.updated_at.strftime(_ISO_FMT) if lp.updated_at else _now_iso()
    return {
        "user_id": lp.user_id,
        "created_at": created,
        "updated_at": updated,
        "topics": topics,
        "preferences": prefs,
        "memory_profile": True,
        "recent_topics": list(lp.recent_topics),
        "assistant_notes_preview": [n.text[:200] for n in lp.assistant_notes[-3:]],
    }


def load_profile(
    user_id: str,
    *,
    storage_dir: str | Path,
    memory_store: Any | None = None,
    concepts_store: Any | None = None,
) -> dict[str, Any]:
    """Load a learner profile from disk.

    When ``memory_store`` is set (A3), profiles are read from ``MemoryStore`` under
    ``memory/profiles/``; otherwise legacy ``learner_profiles/*.json`` is used.
    ``concepts_store`` (defaults to ``memory_store``) supplies ``Concept.mastery_level``
    for accurate ``topics`` mastery in the legacy summary dict.

    Returns:
        The profile dict (always a valid dict with at least ``user_id`` set).
    """
    if memory_store is not None:
        cs = concepts_store if concepts_store is not None else memory_store
        lp = memory_store.load_profile(user_id)
        if lp is not None:
            return _learner_profile_to_dict(lp, concepts_store=cs)
        return _learner_profile_to_dict(memory_store.default_profile(user_id), concepts_store=cs)

    path = _profile_path(user_id, Path(storage_dir))
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load profile %s: %s – using defaults", path, exc)

    return _default_profile(user_id)


def save_profile(
    profile: dict[str, Any],
    *,
    storage_dir: str | Path,
) -> None:
    """Persist a learner profile to disk (atomic write via temp file).

    Args:
        profile: The profile dict; must contain a ``"user_id"`` key.
        storage_dir: Directory where profile JSON files are stored.
    """
    user_id: str = profile.get("user_id", "unknown")
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)

    profile["updated_at"] = _now_iso()
    path = _profile_path(user_id, storage)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.error("Failed to save profile for %s: %s", user_id, exc)
        raise


def update_topic_mastery(
    profile: dict[str, Any],
    topic: str,
    mastery_delta: float,
) -> dict[str, Any]:
    """Adjust mastery for *topic* and increment attempts counter.

    The resulting mastery is clamped to [0.0, 1.0].

    Args:
        profile: Profile dict (mutated in-place AND returned).
        topic: Topic label (e.g. ``"TCP"``).
        mastery_delta: Amount to add (positive) or subtract (negative).

    Returns:
        The updated profile dict.
    """
    topics: dict[str, Any] = profile.setdefault("topics", {})
    entry = topics.setdefault(
        topic,
        {"mastery": 0.0, "attempts": 0, "last_seen": _now_iso()[:10]},
    )
    entry["mastery"] = max(0.0, min(1.0, entry["mastery"] + mastery_delta))
    entry["attempts"] = entry.get("attempts", 0) + 1
    entry["last_seen"] = _now_iso()[:10]
    return profile


def profile_summary(profile: dict[str, Any]) -> str:
    """Return a one-paragraph text summary suitable for injection into the system prompt.

    Args:
        profile: Learner profile dict.

    Returns:
        A short human-readable description of the learner's current state.
    """
    parts: list[str] = []
    if profile.get("memory_profile"):
        rt = profile.get("recent_topics") or []
        if rt:
            parts.append(f"最近学习主题：{', '.join(str(x) for x in rt[:6])}")
        notes = profile.get("assistant_notes_preview") or []
        if notes:
            parts.append(f"备注摘要：{'；'.join(notes[:2])}")

    topics: dict[str, Any] = profile.get("topics", {})
    if not topics and not parts:
        return "学习者尚未有任何学习记录。"

    strong = [t for t, v in topics.items() if v.get("mastery", 0) >= 0.7]
    weak = [t for t, v in topics.items() if v.get("mastery", 0) < 0.4]

    if strong:
        parts.append(f"掌握较好的知识点：{', '.join(strong[:5])}")
    if weak:
        parts.append(f"需要加强的知识点：{', '.join(weak[:5])}")

    prefs: dict[str, str] = profile.get("preferences", {})
    if prefs.get("question_type"):
        parts.append(f"偏好题型：{prefs['question_type']}")
    if prefs.get("learning_style"):
        parts.append(f"学习风格倾向：{prefs['learning_style']}")

    return "；".join(parts) + "。" if parts else "学习者尚无明显的强弱项分布。"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _default_profile(user_id: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
        "topics": {},
        "preferences": {},
    }
