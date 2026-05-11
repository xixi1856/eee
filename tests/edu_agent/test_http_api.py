"""HTTP API + SSE smoke tests (in-memory ASGI)."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from edu_agent.agent import EduAgent
from edu_agent.auth.checker import AuthorizationChecker
from edu_agent.api.server import create_app
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.runner.gateway import Gateway
from edu_agent.types import AgentConfig

from tests.edu_agent.conftest_a5 import FakeEduAgent


@pytest.fixture
def http_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import edu_agent.runner.session_runner as sr

    monkeypatch.setattr(sr, "EduAgent", FakeEduAgent)
    settings = load_settings()
    from edu_agent.sessions.store import SessionStore

    store = SessionStore(tmp_path / "h.sqlite")
    sess = store.create_session("bob")
    seed = EduAgent(
        AgentConfig(session_id=sess.metadata.id, user_id="bob"),
        settings=settings,
        session_store=store,
    )
    cm = ContextManager(
        store,
        ContextConfig(model_max_tokens=seed._max_tokens),
        settings,
        model_name=seed._model,
        summarizer=seed._build_summarizer(),
    )
    del seed
    gw = Gateway(
        settings=settings,
        session_store=store,
        context_manager=cm,
        auth_checker=AuthorizationChecker(expected_api_key=None),
        require_http_key=False,
    )
    app = create_app(gw, session_store=store)
    with TestClient(app) as client:
        yield client, sess.metadata.id, gw, store
    try:
        asyncio.run(asyncio.wait_for(gw.stop(), timeout=12.0))
    except Exception:
        pass
    store.close()


def test_create_and_get_session(http_client):
    client, sid, _gw, _store = http_client
    r = client.get(f"/v1/sessions/{sid}", params={"user_id": "bob"})
    assert r.status_code == 200
    assert r.json()["id"] == sid


def test_sse_stream_lines_are_json(http_client):
    client, sid, _gw, _store = http_client
    body = {
        "model": "x",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    with client.stream(
        "POST",
        "/v1/chat/completions",
        params={"session_id": sid, "user_id": "bob"},
        json=body,
    ) as r:
        assert r.status_code == 200
        buf = r.read()
        text = buf.decode("utf-8")
        for line in text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                payload = line[len("data: ") :]
                obj = json.loads(payload)
                assert "choices" in obj


def test_invalid_session_get_returns_404(http_client):
    client, _sid, _gw, _store = http_client
    r = client.get("/v1/sessions/does-not-exist-xyz", params={"user_id": "bob"})
    assert r.status_code == 404


def test_platform_user_id_mismatch_returns_400(http_client):
    client, sid, _gw, _store = http_client
    body = {
        "model": "x",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    r = client.post(
        "/v1/chat/completions",
        params={"session_id": sid, "user_id": "bob"},
        headers={"X-Platform-User-Id": "not-bob"},
        json=body,
    )
    assert r.status_code == 400
