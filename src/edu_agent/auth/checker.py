"""Authorization checks — invoked only from Gateway."""

from __future__ import annotations

import logging
import os
from typing import Any

from edu_agent.auth.models import AuthContext

logger = logging.getLogger(__name__)


class AuthorizationError(Exception):
    """Caller is not allowed to perform the action."""


class AuthorizationChecker:
    """API key (optional) + session user match."""

    def __init__(self, *, expected_api_key: str | None = None) -> None:
        raw = (expected_api_key or "").strip()
        if not raw:
            raw = (os.environ.get("EDU_AGENT_API_KEY") or "").strip()
        self._expected = raw or None

    def require_http_key_if_configured(self, auth: AuthContext) -> None:
        """Call for HTTP/WebSocket inbound; no-op when no key configured."""
        if not self._expected:
            return
        got = (auth.api_key or "").strip()
        if got != self._expected:
            logger.warning("Invalid or missing API key for user_id=%s", auth.user_id)
            raise AuthorizationError("invalid_api_key")

    def require_session_user(self, auth: AuthContext, *, session_user_id: str) -> None:
        if auth.user_id != session_user_id:
            raise AuthorizationError("session_user_mismatch")


def gateway_auth_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    return dict(raw or {})
