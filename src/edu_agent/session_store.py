"""Session store: append-only JSONL transcript for each user session.

Each session is stored in a separate ``.jsonl`` file under the storage
directory.  Every turn is a single JSON object (one per line) with the
following fields::

    {
      "ts": "2025-05-06T12:00:00",
      "session_id": "abc123",
      "user_id": "alice",
      "role": "user" | "assistant",
      "content": "..."
    }

Design choices
--------------
* Append-only (no in-place edits) → crash-safe, easy to inspect.
* One JSONL file per session → simple; readers can ``grep`` or stream.
* Sessions are identified by ``session_id`` (12-char hex from ``EduAgent``).
* Callers must pass ``storage_dir`` (typically ``EduPaths.sessions_dir``); there is no
  package-level default path to avoid silent writes outside the configured workspace.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel as _PydanticBase  # type: ignore

    def _json_default(obj: object) -> object:
        if isinstance(obj, _PydanticBase):
            return obj.model_dump(mode="json")
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

except ImportError:  # pragma: no cover
    def _json_default(obj: object) -> object:  # type: ignore[misc]
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

logger = logging.getLogger(__name__)

_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def _session_path(session_id: str, storage_dir: Path) -> Path:
    return storage_dir / f"{session_id}.jsonl"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_turn(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    *,
    storage_dir: str | Path,
) -> None:
    """Append one turn to the session's JSONL transcript.

    The storage directory is created automatically if it does not exist.

    Args:
        session_id: Unique session identifier (12-char hex).
        user_id: Learner user identifier.
        role: ``"user"`` or ``"assistant"``.
        content: Text content of the turn.
        storage_dir: Directory where JSONL files are stored.
    """
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    path = _session_path(session_id, storage)

    record: dict[str, Any] = {
        "ts": _now_iso(),
        "session_id": session_id,
        "user_id": user_id,
        "role": role,
        "content": content,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
    except OSError as exc:
        logger.error("Failed to append turn to session %s: %s", session_id, exc)
        raise


def load_session(
    session_id: str,
    *,
    storage_dir: str | Path,
) -> list[dict[str, Any]]:
    """Load all turns from a session JSONL file.

    Returns an empty list if the file does not exist.

    Args:
        session_id: Session identifier.
        storage_dir: Directory where JSONL files are stored.

    Returns:
        List of turn dicts in chronological order.
    """
    path = _session_path(session_id, Path(storage_dir))
    if not path.exists():
        return []

    turns: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed JSONL line in %s: %s", path, exc)
    except OSError as exc:
        logger.error("Failed to read session %s: %s", session_id, exc)
    return turns


def append_message(
    session_id: str,
    user_id: str,
    message: dict[str, Any],
    *,
    storage_dir: str | Path,
) -> None:
    """Append one OpenAI-compatible message to the session's JSONL transcript.

    Supports all OpenAI message roles (``system``, ``user``, ``assistant``,
    ``tool``), including messages that carry ``tool_calls`` arrays.

    The stored record wraps the message with metadata::

        {
          "ts": "2025-05-06T12:00:00",
          "session_id": "abc123",
          "user_id": "alice",
          "role": "assistant",
          "content": null,
          "tool_calls": [...]
        }

    Args:
        session_id: Unique session identifier.
        user_id: Learner user identifier.
        message: A dict conforming to the OpenAI chat message schema.
        storage_dir: Directory where JSONL files are stored.
    """
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    path = _session_path(session_id, storage)

    record: dict[str, Any] = {
        "ts": _now_iso(),
        "session_id": session_id,
        "user_id": user_id,
        **message,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
    except OSError as exc:
        logger.error("Failed to append message to session %s: %s", session_id, exc)
        raise


def list_sessions(*, storage_dir: str | Path) -> list[str]:
    """Return sorted session IDs found in *storage_dir*.

    Args:
        storage_dir: Directory where JSONL files are stored.

    Returns:
        List of session IDs (filename stems), sorted alphabetically.
    """
    storage = Path(storage_dir)
    if not storage.is_dir():
        return []
    return sorted(p.stem for p in storage.glob("*.jsonl"))
