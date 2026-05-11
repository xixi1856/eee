"""Normalize process-wide HTTP proxy env so local services are reachable.

``httpx`` and the ``ollama`` Python client honor ``HTTP_PROXY`` / ``HTTPS_PROXY``.
Without ``NO_PROXY`` covering loopback, traffic to Ollama (``127.0.0.1:11434``) is
sent through the dev proxy (e.g. Clash on ``7890``) and fails when the proxy is
stopped. Wikipedia and similar tools can still pass an explicit proxy per request.
"""

from __future__ import annotations

import os

_LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")


def ensure_loopback_bypass_http_proxy() -> None:
    """Append loopback hosts to ``NO_PROXY`` / ``no_proxy`` if missing (idempotent).

    Skip entirely when ``RAG_DISABLE_LOOPBACK_NO_PROXY`` is ``1`` / ``true`` / ``yes``.
    """
    flag = os.environ.get("RAG_DISABLE_LOOPBACK_NO_PROXY", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return

    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key, "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        seen = set(parts)
        changed = False
        for h in _LOOPBACK_NO_PROXY:
            if h not in seen:
                parts.append(h)
                seen.add(h)
                changed = True
        if changed:
            os.environ[key] = ",".join(parts)
