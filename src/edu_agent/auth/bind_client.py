"""HTTP client for Platform credential binding endpoints."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Hostnames for which we must not honor HTTP_PROXY / HTTPS_PROXY from the environment.
# Browsers often bypass proxies for these; httpx defaults to trust_env=True, which
# breaks `edu bind` to local Next.js when a dev proxy (Clash, etc.) is set but not
# handling localhost correctly.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _httpx_client_kwargs(base_url: str) -> dict[str, Any]:
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        host = ""
    loopback = host in _LOOPBACK_HOSTS
    return {"timeout": 30.0, "trust_env": not loopback}


class BindInvalidError(Exception):
    """Credential code or challenge token is invalid / expired."""


class BindAuthError(Exception):
    """X-Platform-Bind-Key is incorrect."""


class BindRateLimitedError(Exception):
    """Too many failed attempts — try again later."""


class BindNotFoundError(Exception):
    """No binding exists for the given agent_user_id (refresh only)."""


class BindConflictError(Exception):
    """Platform rejected bind (e.g. agent_user_id already tied to another account)."""


def _parse_bind_error_payload(resp: httpx.Response) -> tuple[str, str]:
    """Parse edu-platform JSON errors: ``{ \"error\": { \"code\", \"message\" } }`` or flat legacy."""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return "", ""
    err = data.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "").strip()
        msg = str(err.get("message") or "").strip()
        return code, msg
    code = str(data.get("code") or "").strip()
    msg = str(data.get("message") or "").strip()
    return code, msg


def _raise_for_bind_error(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    code, message = _parse_bind_error_payload(resp)
    detail = ": ".join(p for p in (code, message) if p) or "(empty error body)"
    if resp.status_code == 401:
        raise BindAuthError("Invalid X-Platform-Bind-Key")
    if resp.status_code == 429:
        raise BindRateLimitedError("Rate limited — try again later")
    if resp.status_code == 404:
        raise BindNotFoundError(message or "No binding found for agent_user_id")
    if resp.status_code == 409:
        raise BindConflictError(message or detail)
    raise BindInvalidError(f"Bind failed ({resp.status_code}): {detail}")


async def bind_start(base_url: str, api_key: str, code: str) -> str:
    """Step 1: POST /api/v1/bind/start — validate credential code.

    Returns:
        bind_challenge_token (str): opaque 64-hex token, valid for ~10 min.

    Raises:
        BindInvalidError: code invalid, expired, or already used.
        BindAuthError: wrong API key.
        BindRateLimitedError: IP rate-limited.
    """
    url = f"{base_url.rstrip('/')}/api/v1/bind/start"
    async with httpx.AsyncClient(**_httpx_client_kwargs(base_url)) as client:
        resp = await client.post(
            url,
            json={"code": code},
            headers={"x-platform-bind-key": api_key},
        )
    _raise_for_bind_error(resp)
    return resp.json()["bind_challenge_token"]


async def bind_complete(
    base_url: str,
    api_key: str,
    challenge_token: str,
    agent_user_id: str,
    channel: str,
) -> dict[str, Any]:
    """Step 2: POST /api/v1/bind/complete — finalise binding.

    Returns:
        dict with keys:
            platform_user_id (str): UUID of the Platform user.
            channel_token (str): JWT for subsequent Agent→Platform calls.

    Raises:
        BindInvalidError: challenge expired/consumed, or agent_user_id conflict.
        BindAuthError: wrong API key.
        BindRateLimitedError: IP rate-limited.
    """
    url = f"{base_url.rstrip('/')}/api/v1/bind/complete"
    async with httpx.AsyncClient(**_httpx_client_kwargs(base_url)) as client:
        resp = await client.post(
            url,
            json={
                "bind_challenge_token": challenge_token,
                "agent_user_id": agent_user_id,
                "channel": channel,
            },
            headers={"x-platform-bind-key": api_key},
        )
    _raise_for_bind_error(resp)
    data = resp.json()
    return {
        "platform_user_id": data["platform_user_id"],
        "channel_token": data["channel_token"],
    }


async def refresh_token(
    base_url: str,
    api_key: str,
    agent_user_id: str,
) -> str:
    """POST /api/v1/bind/refresh — exchange agent_user_id for a new channel_token.

    Returns:
        channel_token (str): fresh JWT.

    Raises:
        BindNotFoundError: agent_user_id has no binding.
        BindAuthError: wrong API key.
        BindRateLimitedError: IP rate-limited.
    """
    url = f"{base_url.rstrip('/')}/api/v1/bind/refresh"
    async with httpx.AsyncClient(**_httpx_client_kwargs(base_url)) as client:
        resp = await client.post(
            url,
            json={"agent_user_id": agent_user_id},
            headers={"x-platform-bind-key": api_key},
        )
    _raise_for_bind_error(resp)
    return resp.json()["channel_token"]
