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

def load_profile(user_id: str, *, storage_dir: str | Path) -> dict[str, Any]:
    """Load a learner profile from disk.

    Returns a fresh default profile dict if the file does not exist yet.

    Args:
        user_id: Unique user identifier.
        storage_dir: Directory where profile JSON files are stored.

    Returns:
        The profile dict (always a valid dict with at least ``user_id`` set).
    """
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
    topics: dict[str, Any] = profile.get("topics", {})
    if not topics:
        return "学习者尚未有任何学习记录。"

    strong = [t for t, v in topics.items() if v.get("mastery", 0) >= 0.7]
    weak = [t for t, v in topics.items() if v.get("mastery", 0) < 0.4]

    parts: list[str] = []
    if strong:
        parts.append(f"掌握较好的知识点：{', '.join(strong[:5])}")
    if weak:
        parts.append(f"需要加强的知识点：{', '.join(weak[:5])}")

    prefs: dict[str, str] = profile.get("preferences", {})
    if prefs.get("question_type"):
        parts.append(f"偏好题型：{prefs['question_type']}")

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
