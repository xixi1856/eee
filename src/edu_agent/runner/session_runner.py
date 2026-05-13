"""Per-session FIFO actor: one asyncio worker + bounded queues."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from edu_agent.agent import EduAgent
from edu_agent.bus.models import (
    ChannelKind,
    InboundKind,
    InboundMessage,
    OutboundContentType,
    OutboundMessage,
    ensure_aware_utc,
    new_message_id,
)
from edu_agent.config import EduSettings
from edu_agent.context.manager import ContextManager
from edu_agent.sessions.store import SessionStore
from edu_agent.types import AgentConfig

logger = logging.getLogger(__name__)


class SessionRunnerBusyError(Exception):
    """Inbound queue is full (backpressure)."""


class _ShutdownSentinel:
    pass


_SHUTDOWN = _ShutdownSentinel()


class _OutboundDone:
    pass


_OUTBOUND_DONE = _OutboundDone()


@dataclass
class _WorkItem:
    inbound: InboundMessage
    out_queue: asyncio.Queue[OutboundMessage | _OutboundDone]


class SessionRunner:
    """Exclusive async actor for one session — strict FIFO, no concurrent turns."""

    def __init__(
        self,
        *,
        session_id: str,
        settings: EduSettings,
        session_store: SessionStore,
        context_manager: ContextManager,
        queue_maxsize: int = 100,
        outbound_queue_maxsize: int = 256,
        idle_timeout_sec: float = 1800.0,
    ) -> None:
        self.session_id = session_id
        self._settings = settings
        self._session_store = session_store
        self._context_manager = context_manager
        self._queue_maxsize = max(1, queue_maxsize)
        self._outbound_maxsize = max(16, outbound_queue_maxsize)
        self._idle_timeout_sec = float(idle_timeout_sec)

        self._in_queue: asyncio.Queue[_WorkItem | _ShutdownSentinel] = asyncio.Queue(
            maxsize=self._queue_maxsize
        )
        self._worker: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._last_active = time.monotonic()
        self._current_turn_task: asyncio.Task[None] | None = None
        self._cancel_requested = False  # reserved for richer cancel semantics

        self._agent = EduAgent(
            settings=settings,
            session_store=session_store,
            context_manager=context_manager,
            config=AgentConfig(session_id=session_id, user_id="pending"),
        )

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._stop_requested = False
            self._worker = asyncio.create_task(
                self._worker_loop(),
                name=f"edu-session-runner-{self.session_id}",
            )

    async def stop(self) -> None:
        self._stop_requested = True
        try:
            self._in_queue.put_nowait(_SHUTDOWN)
        except asyncio.QueueFull:
            pass
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=60.0)
            except asyncio.TimeoutError:
                self._worker.cancel()
                try:
                    await self._worker
                except asyncio.CancelledError:
                    pass
            self._worker = None

    def touch_activity(self) -> None:
        self._last_active = time.monotonic()

    def idle_expired(self) -> bool:
        return (time.monotonic() - self._last_active) > self._idle_timeout_sec

    async def enqueue_and_stream(self, inbound: InboundMessage) -> AsyncIterator[OutboundMessage]:
        """Queue one inbound work item and yield outbound chunks until turn completes."""
        if self._stop_requested:
            raise RuntimeError("SessionRunner is stopped")
        self.start()
        out_q: asyncio.Queue[OutboundMessage | _OutboundDone] = asyncio.Queue(
            maxsize=self._outbound_maxsize
        )
        item = _WorkItem(inbound=inbound, out_queue=out_q)
        try:
            self._in_queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise SessionRunnerBusyError("session inbound queue is full") from exc
        self.touch_activity()
        try:
            while True:
                chunk = await out_q.get()
                if chunk is _OUTBOUND_DONE:
                    break
                assert isinstance(chunk, OutboundMessage)
                yield chunk
        finally:
            # Drain stray items if consumer disconnects early
            while not out_q.empty():
                try:
                    out_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def _worker_loop(self) -> None:
        while not self._stop_requested:
            try:
                work = await asyncio.wait_for(self._in_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                if self.idle_expired():
                    logger.info("SessionRunner idle timeout session_id=%s", self.session_id)
                    break
                continue

            if work is _SHUTDOWN:
                break

            assert isinstance(work, _WorkItem)
            self.touch_activity()
            try:
                await self._process_work_item(work)
            except Exception as exc:  # noqa: BLE001
                logger.exception("SessionRunner turn failed: %s", exc)
                err = OutboundMessage(
                    message_id=new_message_id(),
                    in_reply_to=work.inbound.message_id,
                    session_id=self.session_id,
                    user_id=work.inbound.user_id,
                    content=str(exc),
                    content_type=OutboundContentType.ERROR,
                    is_final=True,
                    metadata={"error_type": type(exc).__name__},
                )
                try:
                    work.out_queue.put_nowait(err)
                except asyncio.QueueFull:
                    pass
            finally:
                try:
                    await work.out_queue.put(_OUTBOUND_DONE)
                except Exception:  # noqa: BLE001
                    pass

    async def _process_work_item(self, work: _WorkItem) -> None:
        inbound = work.inbound
        out_q = work.out_queue
        self._agent.config.user_id = inbound.user_id

        if inbound.kind == InboundKind.INTERRUPT:
            self._cancel_requested = False
            msg = OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=self.session_id,
                user_id=inbound.user_id,
                timestamp=ensure_aware_utc(),
                content="interrupt acknowledged",
                content_type=OutboundContentType.META,
                is_final=True,
                metadata={"kind": "interrupt"},
            )
            await out_q.put(msg)
            return

        if inbound.kind == InboundKind.CANCEL:
            if self._current_turn_task and not self._current_turn_task.done():
                self._current_turn_task.cancel()
                try:
                    await self._current_turn_task
                except asyncio.CancelledError:
                    pass
                self._current_turn_task = None
            msg = OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=self.session_id,
                user_id=inbound.user_id,
                timestamp=ensure_aware_utc(),
                content="cancel requested",
                content_type=OutboundContentType.META,
                is_final=True,
                metadata={"kind": "cancel"},
            )
            await out_q.put(msg)
            return

        if inbound.kind != InboundKind.USER_TEXT:
            return

        cid = str(inbound.metadata.get("platform_course_id") or "").strip()
        lid = str(inbound.metadata.get("platform_lesson_id") or "").strip()
        trace_id = str(inbound.metadata.get("trace_id") or "").strip()
        debug_trace = bool(inbound.metadata.get("debug_trace", False))
        self._agent.config.course_id = cid
        self._agent.config.lesson_id = lid
        self._agent.config.trace_id = trace_id
        self._agent.config.debug_trace = debug_trace

        logger.debug(
            "SessionRunner turn_start session_id=%s user_id=%s trace_id=%s course_id=%s lesson_id=%s",
            self.session_id,
            inbound.user_id,
            trace_id,
            cid,
            lid,
        )

        if inbound.channel == ChannelKind.CLI:
            from edu_agent.cli import build_callbacks

            self._agent.callbacks = build_callbacks(
                str(inbound.metadata.get("cli_progress", "off"))
            )
        else:
            self._agent.callbacks = None

        async def _turn() -> None:
            async for ob in self._agent.run_turn_stream(
                inbound.content,
                attachments=inbound.attachments,
                in_reply_to=inbound.message_id,
            ):
                await out_q.put(ob)

        self._current_turn_task = asyncio.create_task(_turn(), name=f"turn-{self.session_id}")
        try:
            await self._current_turn_task
        finally:
            self._current_turn_task = None

    def trigger_context_compress(self) -> None:
        """Forward to the session-bound agent (CLI /compress-context)."""
        self._agent.trigger_context_compress()

    def finalize_memory_session(self) -> None:
        self._agent.finalize_memory_session()
