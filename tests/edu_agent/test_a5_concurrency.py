"""Multi-session concurrency (10 x 10) with fake agent."""

from __future__ import annotations

import asyncio

import pytest

from edu_agent.agent import EduAgent
from edu_agent.auth.checker import AuthorizationChecker
from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.runner.gateway import Gateway
from edu_agent.types import AgentConfig

from tests.edu_agent.conftest_a5 import FakeEduAgent


@pytest.mark.asyncio
async def test_ten_sessions_ten_messages_no_deadlock(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import edu_agent.runner.session_runner as sr

    monkeypatch.setattr(sr, "EduAgent", FakeEduAgent)
    settings = load_settings()
    from edu_agent.sessions.store import SessionStore

    store = SessionStore(tmp_path / "c.sqlite")
    sessions = [store.create_session("u") for _ in range(10)]
    seed = EduAgent(AgentConfig(), settings=settings, session_store=store)
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
        queue_maxsize=200,
        max_runners=32,
        require_http_key=False,
    )

    async def session_worker(sid: str) -> list[str]:
        auth = AuthContext(user_id="u", channel="cli")
        out: list[str] = []
        for i in range(10):
            inbound = InboundMessage.user_text(
                channel=ChannelKind.CLI,
                session_id=sid,
                user_id="u",
                content=f"{sid}-{i}",
            )
            async for ob in gw.process_inbound_message(inbound, auth):
                if ob.is_final and ob.content:
                    out.append(ob.content)
        return out

    tasks = [asyncio.create_task(session_worker(s.metadata.id)) for s in sessions]
    results = await asyncio.gather(*tasks)
    for seq in results:
        assert len(seq) == 10
    await gw.stop()
    store.close()
