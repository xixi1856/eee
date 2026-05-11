"""Tests for bind_client and token_store."""

from __future__ import annotations

import json
import time

import pytest
import respx
import httpx

from edu_agent.auth.bind_client import (
    bind_start,
    bind_complete,
    refresh_token,
    BindInvalidError,
    BindAuthError,
    BindRateLimitedError,
    BindNotFoundError,
    BindConflictError,
)
from edu_agent.auth import token_store as ts


BASE_URL = "http://test-platform"
API_KEY = "test-bind-key-16chars"


# ---------------------------------------------------------------------------
# bind_client tests
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_bind_start_success():
    respx.post(f"{BASE_URL}/api/v1/bind/start").mock(
        return_value=httpx.Response(200, json={"bind_challenge_token": "aabbcc" * 10 + "aabb"})
    )
    token = await bind_start(BASE_URL, API_KEY, "Abc12345")
    assert token == "aabbcc" * 10 + "aabb"


def _err(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "details": {}}}


@respx.mock
@pytest.mark.asyncio
async def test_bind_start_invalid_code():
    respx.post(f"{BASE_URL}/api/v1/bind/start").mock(
        return_value=httpx.Response(400, json=_err("BIND_INVALID", "bad code"))
    )
    with pytest.raises(BindInvalidError, match="BIND_INVALID"):
        await bind_start(BASE_URL, API_KEY, "badcode1")


@respx.mock
@pytest.mark.asyncio
async def test_bind_start_invalid_code_flat_legacy_body_still_parsed():
    respx.post(f"{BASE_URL}/api/v1/bind/start").mock(
        return_value=httpx.Response(400, json={"code": "BIND_INVALID", "message": "legacy"})
    )
    with pytest.raises(BindInvalidError, match="BIND_INVALID"):
        await bind_start(BASE_URL, API_KEY, "badcode1")


@respx.mock
@pytest.mark.asyncio
async def test_bind_start_wrong_api_key():
    respx.post(f"{BASE_URL}/api/v1/bind/start").mock(
        return_value=httpx.Response(401, json=_err("UNAUTHORIZED", "nope"))
    )
    with pytest.raises(BindAuthError):
        await bind_start(BASE_URL, "wrong-key", "Abc12345")


@respx.mock
@pytest.mark.asyncio
async def test_bind_start_rate_limited():
    respx.post(f"{BASE_URL}/api/v1/bind/start").mock(
        return_value=httpx.Response(429, json=_err("RATE_LIMITED", "slow down"))
    )
    with pytest.raises(BindRateLimitedError):
        await bind_start(BASE_URL, API_KEY, "Abc12345")


@respx.mock
@pytest.mark.asyncio
async def test_bind_complete_success():
    respx.post(f"{BASE_URL}/api/v1/bind/complete").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "platform_user_id": "platform-uuid",
                "channel_token": "jwt.token.here",
            },
        )
    )
    result = await bind_complete(BASE_URL, API_KEY, "challenge-token", "agent-id", "cli")
    assert result["platform_user_id"] == "platform-uuid"
    assert result["channel_token"] == "jwt.token.here"


@respx.mock
@pytest.mark.asyncio
async def test_bind_complete_invalid_challenge():
    respx.post(f"{BASE_URL}/api/v1/bind/complete").mock(
        return_value=httpx.Response(
            400, json=_err("BIND_INVALID", "Invalid or expired bind challenge")
        )
    )
    with pytest.raises(BindInvalidError, match="BIND_INVALID"):
        await bind_complete(BASE_URL, API_KEY, "expired-challenge", "agent-id", "cli")


@respx.mock
@pytest.mark.asyncio
async def test_bind_complete_agent_conflict_409():
    respx.post(f"{BASE_URL}/api/v1/bind/complete").mock(
        return_value=httpx.Response(
            409,
            json=_err(
                "CONFLICT",
                "This agent_user_id is already bound to another platform account.",
            ),
        )
    )
    with pytest.raises(BindConflictError, match="already bound"):
        await bind_complete(BASE_URL, API_KEY, "a" * 64, "agent-id", "cli")


@respx.mock
@pytest.mark.asyncio
async def test_refresh_token_success():
    respx.post(f"{BASE_URL}/api/v1/bind/refresh").mock(
        return_value=httpx.Response(200, json={"channel_token": "new.jwt.token"})
    )
    new_token = await refresh_token(BASE_URL, API_KEY, "agent-id")
    assert new_token == "new.jwt.token"


@respx.mock
@pytest.mark.asyncio
async def test_refresh_token_not_found():
    respx.post(f"{BASE_URL}/api/v1/bind/refresh").mock(
        return_value=httpx.Response(404, json=_err("BIND_NOT_FOUND", "missing"))
    )
    with pytest.raises(BindNotFoundError):
        await refresh_token(BASE_URL, API_KEY, "unbound-agent")


# ---------------------------------------------------------------------------
# token_store tests
# ---------------------------------------------------------------------------


def test_token_store_save_and_load(tmp_path):
    store_path = tmp_path / "identity.json"
    identity = {
        "agent_user_id": "agent-001",
        "platform_user_id": "platform-uuid",
        "channel": "cli",
        "channel_token": "jwt.token",
        "bound_at": "2026-01-01T00:00:00+00:00",
        "token_exp": int(time.time()) + 3600,
    }
    ts.save(identity, path=store_path)
    loaded = ts.load(path=store_path)
    assert loaded is not None
    assert loaded["agent_user_id"] == "agent-001"
    assert loaded["platform_user_id"] == "platform-uuid"


def test_token_store_load_missing_file(tmp_path):
    result = ts.load(path=tmp_path / "nonexistent.json")
    assert result is None


def test_token_store_clear(tmp_path):
    store_path = tmp_path / "identity.json"
    ts.save({"agent_user_id": "x", "channel_token": "t"}, path=store_path)
    assert store_path.exists()
    ts.clear(path=store_path)
    assert not store_path.exists()
    # clear on missing file is a no-op
    ts.clear(path=store_path)


def test_token_store_corrupted_file(tmp_path):
    store_path = tmp_path / "identity.json"
    store_path.write_text("not-valid-json", encoding="utf-8")
    result = ts.load(path=store_path)
    assert result is None


def test_token_store_wrong_type(tmp_path):
    store_path = tmp_path / "identity.json"
    store_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = ts.load(path=store_path)
    assert result is None
