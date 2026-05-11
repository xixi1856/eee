"""Persistent local storage for the Agent ↔ Platform identity binding."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default path: ~/.edu_agent/identity.json
_DEFAULT_PATH = Path.home() / ".edu_agent" / "identity.json"


def _resolve_path(path: str | Path | None) -> Path:
    return Path(path).expanduser().resolve() if path else _DEFAULT_PATH


def save(identity: dict[str, Any], *, path: str | Path | None = None) -> None:
    """Persist binding identity to disk (mode 0o600).

    identity keys:
        agent_user_id (str)
        platform_user_id (str)
        channel (str)
        channel_token (str)
        bound_at (str, ISO-8601)
        token_exp (int, unix timestamp)
    """
    dest = _resolve_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
        # Restrict permissions before final rename (only owner can read/write)
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except NotImplementedError:
            pass  # Windows — best-effort
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.debug("Identity saved to %s", dest)


def load(*, path: str | Path | None = None) -> dict[str, Any] | None:
    """Load binding identity from disk.

    Returns None if the file does not exist or cannot be parsed.
    """
    dest = _resolve_path(path)
    if not dest.exists():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("Identity file %s has unexpected format", dest)
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load identity from %s: %s", dest, exc)
        return None


def clear(*, path: str | Path | None = None) -> None:
    """Remove the identity file (no-op if absent)."""
    dest = _resolve_path(path)
    dest.unlink(missing_ok=True)
    logger.debug("Identity cleared at %s", dest)
