"""Single entrypoint: auth, routing, runner lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable
from typing import Any

from edu_agent.auth.checker import AuthorizationChecker, AuthorizationError
from edu_agent.auth.models import AuthContext
from edu_agent.auth import bind_client, token_store
from edu_agent.bus.models import (
    InboundMessage,
    OutboundContentType,
    OutboundMessage,
    new_message_id,
)
from edu_agent.config import EduSettings
from edu_agent.context.manager import ContextManager
from edu_agent.runner.session_runner import SessionRunner, SessionRunnerBusyError
from edu_agent.sessions.store import SessionNotFoundError, SessionStore

logger = logging.getLogger(__name__)


class Gateway:
    """Routes all inbound messages to per-session FIFO runners."""

    def __init__(
        self,
        *,
        settings: EduSettings,
        session_store: SessionStore,
        context_manager: ContextManager,
        auth_checker: AuthorizationChecker,
        queue_maxsize: int = 100,
        outbound_queue_maxsize: int = 256,
        runner_idle_timeout_sec: float = 1800.0,
        max_runners: int = 256,
        require_http_key: bool = False,
        require_binding: bool = False,
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._context_manager = context_manager
        self._auth = auth_checker
        self._queue_maxsize = queue_maxsize
        self._outbound_maxsize = outbound_queue_maxsize
        self._runner_idle = runner_idle_timeout_sec
        self._max_runners = max(1, max_runners)
        self._require_http_key = require_http_key

        self._require_binding = require_binding
        self._runners: OrderedDict[str, SessionRunner] = OrderedDict()
        self._adapters: list[Any] = []
        self._closing = False
        # Protects _runners only. Do not nest: _ensure_runner() acquires this lock; callers
        # must not hold it while awaiting _ensure_runner (asyncio.Lock is not reentrant).
        self._lock = asyncio.Lock()

    @property
    def settings(self) -> EduSettings:
        """App-scoped settings (same object used to build ``ContextManager`` / runners)."""
        return self._settings

    def verify_http_optional(self, auth: AuthContext) -> None:
        """Enforce API key for HTTP/WebSocket only (IM channels skip HTTP API key)."""
        if auth.channel not in ("http", "websocket"):
            return
        self._auth.require_http_key_if_configured(auth)

    def ensure_session_owner(self, auth: AuthContext, *, session_user_id: str) -> None:
        self._auth.require_session_user(auth, session_user_id=session_user_id)

    async def cli_abandon_session(self, session_id: str, auth: AuthContext) -> str:
        """Stop runner for *session_id* and allocate a new session row (CLI /reset)."""
        sess = self._session_store.get_session(session_id)
        if sess is None:
            raise ValueError("unknown session")
        self._auth.require_session_user(auth, session_user_id=sess.metadata.user_id)
        async with self._lock:
            old = self._runners.pop(session_id, None)
        if old is not None:
            await old.stop()
        new_sess = self._session_store.create_session(auth.user_id)
        return new_sess.metadata.id

    async def cli_compress_context(self, session_id: str, auth: AuthContext) -> bool:
        """Trigger context compression on the active runner if any. Returns False if no runner."""
        sess = self._session_store.get_session(session_id)
        if sess is None:
            return False
        self._auth.require_session_user(auth, session_user_id=sess.metadata.user_id)
        async with self._lock:
            runner = self._runners.get(session_id)
        if runner is None:
            return False
        runner.trigger_context_compress()
        return True

    def register_adapter(self, adapter: Any) -> None:
        self._adapters.append(adapter)

    async def process_inbound_message(
        self,
        inbound: InboundMessage,
        auth: AuthContext,
    ) -> AsyncIterator[OutboundMessage]:
        """Authorize, resolve session, forward to ``SessionRunner`` (exclusive path)."""
        logger.debug(
            "Gateway inbound session_id=%s user_id=%s channel=%s trace_id=%s message_id=%s",
            inbound.session_id,
            inbound.user_id,
            inbound.channel.value,
            str(inbound.metadata.get("trace_id") or ""),
            str(inbound.message_id),
        )
        if self._closing:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="gateway is shutting down",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "shutting_down"},
            )
            return

        try:
            self.verify_http_optional(auth)
        except AuthorizationError:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="unauthorized",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "unauthorized"},
            )
            return

        # --- Binding verification + automatic token refresh ---
        if not self._require_binding:
            # Binding enforcement disabled — skip identity check.
            try:
                sess = self._session_store.get_session(inbound.session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("get_session failed: %s", exc)
                sess = None
            if sess is None:
                yield OutboundMessage(
                    message_id=new_message_id(),
                    in_reply_to=inbound.message_id,
                    session_id=inbound.session_id,
                    user_id=auth.user_id,
                    content="session not found",
                    content_type=OutboundContentType.ERROR,
                    is_final=True,
                    metadata={"code": "session_not_found"},
                )
                return
            try:
                self._auth.require_session_user(auth, session_user_id=sess.metadata.user_id)
            except AuthorizationError:
                yield OutboundMessage(
                    message_id=new_message_id(),
                    in_reply_to=inbound.message_id,
                    session_id=inbound.session_id,
                    user_id=auth.user_id,
                    content="forbidden",
                    content_type=OutboundContentType.ERROR,
                    is_final=True,
                    metadata={"code": "forbidden"},
                )
                return
            runner = await self._ensure_runner(inbound.session_id)
            try:
                async for ob in runner.enqueue_and_stream(inbound):
                    yield ob
            except SessionRunnerBusyError:
                yield OutboundMessage(
                    message_id=new_message_id(),
                    in_reply_to=inbound.message_id,
                    session_id=inbound.session_id,
                    user_id=auth.user_id,
                    content="session queue full",
                    content_type=OutboundContentType.ERROR,
                    is_final=True,
                    metadata={"code": "queue_full"},
                )
            return

        identity = token_store.load()
        if identity is None or not identity.get("channel_token"):
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="agent not bound to platform user — run `edu bind` first",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "identity_not_bound"},
            )
            return

        channel_token: str = identity["channel_token"]
        try:
            AuthorizationChecker.validate_channel_token(channel_token)
        except AuthorizationError as _tok_err:
            if str(_tok_err) == "channel_token_expired":
                # Auto-refresh via /bind/refresh
                bind_key = (self._settings.platform_bind_key or "").strip()
                agent_uid = identity.get("agent_user_id", "")
                if bind_key and agent_uid:
                    try:
                        channel_token = await bind_client.refresh_token(
                            self._settings.platform_base_url, bind_key, agent_uid
                        )
                        identity["channel_token"] = channel_token
                        token_store.save(identity)
                        logger.info("channel_token refreshed for agent_user_id=%s", agent_uid)
                    except Exception as _ref_exc:  # noqa: BLE001
                        logger.warning("channel_token refresh failed: %s", _ref_exc)
                        yield OutboundMessage(
                            message_id=new_message_id(),
                            in_reply_to=inbound.message_id,
                            session_id=inbound.session_id,
                            user_id=auth.user_id,
                            content="channel token expired and refresh failed — run `edu bind` again",
                            content_type=OutboundContentType.ERROR,
                            is_final=True,
                            metadata={"code": "channel_token_refresh_failed"},
                        )
                        return
                else:
                    yield OutboundMessage(
                        message_id=new_message_id(),
                        in_reply_to=inbound.message_id,
                        session_id=inbound.session_id,
                        user_id=auth.user_id,
                        content="channel token expired — run `edu bind` again",
                        content_type=OutboundContentType.ERROR,
                        is_final=True,
                        metadata={"code": "channel_token_expired"},
                    )
                    return
            else:
                yield OutboundMessage(
                    message_id=new_message_id(),
                    in_reply_to=inbound.message_id,
                    session_id=inbound.session_id,
                    user_id=auth.user_id,
                    content="invalid channel token",
                    content_type=OutboundContentType.ERROR,
                    is_final=True,
                    metadata={"code": "invalid_channel_token"},
                )
                return

        # Inject platform identity into auth context
        auth = auth.model_copy(
            update={
                "token": channel_token,
                "platform_user_id": identity.get("platform_user_id"),
            }
        )
        # --- End binding verification ---

        try:
            sess = self._session_store.get_session(inbound.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_session failed: %s", exc)
            sess = None
        if sess is None:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="session not found",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "session_not_found"},
            )
            return

        try:
            self._auth.require_session_user(auth, session_user_id=sess.metadata.user_id)
        except AuthorizationError:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="forbidden",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "forbidden"},
            )
            return

        runner = await self._ensure_runner(inbound.session_id)

        try:
            async for ob in runner.enqueue_and_stream(inbound):
                yield ob
        except SessionRunnerBusyError:
            yield OutboundMessage(
                message_id=new_message_id(),
                in_reply_to=inbound.message_id,
                session_id=inbound.session_id,
                user_id=auth.user_id,
                content="session queue full",
                content_type=OutboundContentType.ERROR,
                is_final=True,
                metadata={"code": "queue_full"},
            )

    async def _ensure_runner(self, session_id: str) -> SessionRunner:
        evicted: list[SessionRunner] = []
        async with self._lock:
            if session_id in self._runners:
                runner = self._runners[session_id]
                self._runners.move_to_end(session_id, last=True)
                return runner
            while len(self._runners) >= self._max_runners:
                _sid, old = self._runners.popitem(last=False)
                evicted.append(old)
        for old in evicted:
            await old.stop()
        async with self._lock:
            if session_id in self._runners:
                r = self._runners[session_id]
                self._runners.move_to_end(session_id, last=True)
                return r
            runner = SessionRunner(
                session_id=session_id,
                settings=self._settings,
                session_store=self._session_store,
                context_manager=self._context_manager,
                queue_maxsize=self._queue_maxsize,
                outbound_queue_maxsize=self._outbound_maxsize,
                idle_timeout_sec=self._runner_idle,
            )
            self._runners[session_id] = runner
            return runner

    async def start(self) -> None:
        for ad in self._adapters:
            start_fn = getattr(ad, "start", None)
            if start_fn is not None:
                r = start_fn()
                if isinstance(r, Awaitable):
                    await r

    async def stop(self) -> None:
        """Graceful shutdown: adapters, then runners."""
        self._closing = True
        for ad in reversed(self._adapters):
            stop_fn = getattr(ad, "stop", None)
            if stop_fn is not None:
                r = stop_fn()
                if isinstance(r, Awaitable):
                    await r
        async with self._lock:
            runners = list(self._runners.values())
            self._runners.clear()
        for r in runners:
            try:
                r.finalize_memory_session()
            except Exception as exc:  # noqa: BLE001
                logger.warning("finalize_memory_session: %s", exc)
            await r.stop()
