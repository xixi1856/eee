"""SessionRunner FIFO, queue bounds, and control messages."""

from __future__ import annotations

import asyncio

import pytest

from edu_agent.agent import EduAgent
from edu_agent.bus.models import ChannelKind, InboundKind, InboundMessage
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.runner.session_runner import SessionRunner
from edu_agent.types import AgentConfig

from tests.edu_agent.conftest_a5 import FakeEduAgent


@pytest.fixture
def patched_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    import edu_agent.runner.session_runner as sr

    monkeypatch.setattr(sr, "EduAgent", FakeEduAgent)


@pytest.fixture
def runner_stack(tmp_path, patched_agent: None):
    settings = load_settings()
    db = tmp_path / "s.sqlite"
    from edu_agent.sessions.store import SessionStore

    store = SessionStore(db)
    sess = store.create_session("u1")
    seed = EduAgent(
        AgentConfig(session_id=sess.metadata.id, user_id="u1"),
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
    runner = SessionRunner(
        session_id=sess.metadata.id,
        settings=settings,
        session_store=store,
        context_manager=cm,
        queue_maxsize=3,
        outbound_queue_maxsize=32,
        idle_timeout_sec=600.0,
    )
    return runner, store, sess.metadata.id, cm


@pytest.mark.asyncio
async def test_fifo_two_messages_sequential(runner_stack):
    runner, _store, sid, _cm = runner_stack
    out1: list[str] = []
    async for ob in runner.enqueue_and_stream(
        InboundMessage.user_text(
            channel=ChannelKind.CLI,
            session_id=sid,
            user_id="u1",
            content="ab",
        )
    ):
        if ob.content.endswith("ab") and ob.is_final:
            out1.append("done1")
    out2: list[str] = []
    async for ob in runner.enqueue_and_stream(
        InboundMessage.user_text(
            channel=ChannelKind.CLI,
            session_id=sid,
            user_id="u1",
            content="cd",
        )
    ):
        if ob.content.endswith("cd") and ob.is_final:
            out2.append("done2")
    assert out1 == ["done1"]
    assert out2 == ["done2"]
    await runner.stop()


@pytest.mark.asyncio
async def test_platform_metadata_injects_course_and_lesson(runner_stack):
    runner, _store, sid, _cm = runner_stack
    async for _ob in runner.enqueue_and_stream(
        InboundMessage.user_text(
            channel=ChannelKind.HTTP,
            session_id=sid,
            user_id="u1",
            content="hi",
            metadata={
                "platform_course_id": "course-uuid-1",
                "platform_lesson_id": "lesson-uuid-2",
            },
        )
    ):
        pass
    assert runner._agent.config.course_id == "course-uuid-1"
    assert runner._agent.config.lesson_id == "lesson-uuid-2"
    await runner.stop()


@pytest.mark.asyncio
async def test_no_concurrent_turns(runner_stack, monkeypatch: pytest.MonkeyPatch):
    """Two concurrent enqueue_and_stream calls on the *same* runner: FIFO orders completions."""
    runner, _store, sid, cm = runner_stack
    await runner.stop()

    import edu_agent.runner.session_runner as sr

    class SlowAgent(FakeEduAgent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._delay = 0.15

    monkeypatch.setattr(sr, "EduAgent", SlowAgent)
    settings = load_settings()
    runner2 = SessionRunner(
        session_id=sid,
        settings=settings,
        session_store=_store,
        context_manager=cm,
        queue_maxsize=5,
        idle_timeout_sec=600.0,
    )
    order: list[int] = []

    async def first() -> None:
        async for ob in runner2.enqueue_and_stream(
            InboundMessage.user_text(
                channel=ChannelKind.CLI, session_id=sid, user_id="u1", content="111"
            )
        ):
            if ob.is_final:
                order.append(1)

    async def second() -> None:
        await asyncio.sleep(0.02)
        async for ob in runner2.enqueue_and_stream(
            InboundMessage.user_text(
                channel=ChannelKind.CLI, session_id=sid, user_id="u1", content="222"
            )
        ):
            if ob.is_final:
                order.append(2)

    await asyncio.gather(first(), second())
    assert order == [1, 2]
    await runner2.stop()


@pytest.mark.asyncio
async def test_cancel_control_message(runner_stack):
    runner, _store, sid, _cm = runner_stack
    cancel_in = InboundMessage.control(
        kind=InboundKind.CANCEL,
        channel=ChannelKind.CLI,
        session_id=sid,
        user_id="u1",
    )
    outs = [ob async for ob in runner.enqueue_and_stream(cancel_in)]
    assert outs and outs[-1].metadata.get("kind") == "cancel"
    await runner.stop()
