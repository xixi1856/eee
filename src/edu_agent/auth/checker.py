"""Authorization checks — invoked only from Gateway."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
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

    @staticmethod
    def validate_channel_token(token: str) -> dict[str, Any]:
        """Decode a channel JWT payload and verify typ + expiry (no signature check).

        Returns the payload dict on success.

        Raises:
            AuthorizationError: if the token is malformed, wrong type, or expired.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise AuthorizationError("invalid_channel_token")
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        except AuthorizationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AuthorizationError("invalid_channel_token") from exc

        if payload.get("typ") != "channel":
            raise AuthorizationError("invalid_channel_token")

        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise AuthorizationError("channel_token_expired")

        return payload

    @staticmethod
    def require_bound_identity(auth: AuthContext) -> None:
        """Raise AuthorizationError if auth carries no channel_token."""
        if not auth.token:
            raise AuthorizationError("identity_not_bound")


def gateway_auth_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    return dict(raw or {})
