"""Gateway routing, auth, shutdown."""

from __future__ import annotations

import pytest

from edu_agent.agent import EduAgent
from edu_agent.auth.checker import AuthorizationChecker
from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.runner.gateway import Gateway
from edu_agent.types import AgentConfig

from tests.edu_agent.conftest_a5 import FakeEduAgent


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    import edu_agent.runner.session_runner as sr

    monkeypatch.setattr(sr, "EduAgent", FakeEduAgent)


@pytest.fixture
def gateway(tmp_path, fake_agent: None):
    settings = load_settings()
    from edu_agent.sessions.store import SessionStore

    store = SessionStore(tmp_path / "g.sqlite")
    sess = store.create_session("alice")
    seed = EduAgent(
        AgentConfig(session_id=sess.metadata.id, user_id="alice"),
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
        queue_maxsize=10,
        max_runners=8,
        require_http_key=False,
    )
    return gw, store, sess.metadata.id


@pytest.mark.asyncio
async def test_process_routes_to_runner(gateway):
    gw, _store, sid = gateway
    auth = AuthContext(user_id="alice", channel="cli")
    inbound = InboundMessage.user_text(
        channel=ChannelKind.CLI,
        session_id=sid,
        user_id="alice",
        content="hello",
    )
    finals = [
        ob
        async for ob in gw.process_inbound_message(inbound, auth)
        if ob.content_type == OutboundContentType.TEXT and ob.is_final
    ]
    assert finals and finals[-1].content == "hello"
    await gw.stop()


@pytest.mark.asyncio
async def test_wrong_user_forbidden(gateway):
    gw, _store, sid = gateway
    auth = AuthContext(user_id="eve", channel="cli")
    inbound = InboundMessage.user_text(
        channel=ChannelKind.CLI,
        session_id=sid,
        user_id="eve",
        content="x",
    )
    errs = [
        ob
        async for ob in gw.process_inbound_message(inbound, auth)
        if ob.content_type == OutboundContentType.ERROR
    ]
    assert errs and errs[0].metadata.get("code") == "forbidden"
    await gw.stop()


@pytest.mark.asyncio
async def test_graceful_shutdown(gateway):
    gw, _store, sid = gateway
    auth = AuthContext(user_id="alice", channel="cli")
    inbound = InboundMessage.user_text(
        channel=ChannelKind.CLI, session_id=sid, user_id="alice", content="a"
    )
    async for _ in gw.process_inbound_message(inbound, auth):
        pass
    await gw.stop()
