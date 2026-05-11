"""Feishu / Lark enterprise bot — WebSocket long connection (``lark-oapi``).

The Lark ``ws.Client`` binds to the ``asyncio`` loop active at **first import** of
``lark_oapi.ws.client`` and uses ``run_until_complete`` internally, so it must **not**
share the uvicorn/Gateway event loop. We run the SDK in a **dedicated thread** that
sets a fresh loop before importing the client module, and forward IM events to the
Gateway via ``asyncio.run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType, OutboundMessage
from edu_agent.channels.base import ChannelAdapter
from edu_agent.config import FeishuChannelSettings
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore

logger = logging.getLogger(__name__)

_TENANT_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
_SEND_MESSAGE_PATH = "/open-apis/im/v1/messages"


@dataclass(frozen=True)
class FeishuImInbound:
    """Parsed ``im.message.receive_v1`` (text, P2P only for MVP)."""

    text: str
    open_id: str
    chat_id: str
    message_id: str


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    md = getattr(obj, "model_dump", None)
    if callable(md):
        return md(mode="json")  # type: ignore[no-any-return]
    d = getattr(obj, "dict", None)
    if callable(d):
        return d()  # type: ignore[no-any-return]
    if hasattr(obj, "__dict__"):
        raw = vars(obj)
        return {str(k): v for k, v in raw.items() if not str(k).startswith("_")}
    return {}


def _parse_im_message_receive_v1(data: Any) -> FeishuImInbound | None:
    """Extract text + sender from ``P2ImMessageReceiveV1`` (object or dict)."""
    event_obj = getattr(data, "event", None)
    if event_obj is not None:
        event = _as_dict(event_obj)
    elif isinstance(data, dict):
        ev = data.get("event")
        event = ev if isinstance(ev, dict) else {}
    else:
        return None
    if not event:
        return None
    msg_raw = event.get("message")
    message = msg_raw if isinstance(msg_raw, dict) else _as_dict(msg_raw)
    if not message:
        return None
    if str(message.get("message_type") or "") != "text":
        return None
    if str(message.get("chat_type") or "") != "p2p":
        logger.debug("Feishu: skip non-p2p chat_type=%s", message.get("chat_type"))
        return None
    snd_raw = event.get("sender")
    sender = snd_raw if isinstance(snd_raw, dict) else _as_dict(snd_raw)
    if not sender:
        return None
    if str(sender.get("sender_type") or "") != "user":
        return None
    sid_raw = sender.get("sender_id")
    sender_id = sid_raw if isinstance(sid_raw, dict) else _as_dict(sid_raw)
    if not sender_id:
        return None
    open_id = str(sender_id.get("open_id") or "").strip()
    if not open_id:
        return None
    raw_content = message.get("content")
    if isinstance(raw_content, dict):
        text = str(raw_content.get("text") or "").strip()
    else:
        try:
            parsed = json.loads(str(raw_content or "{}"))
            text = str(parsed.get("text") or "").strip()
        except (json.JSONDecodeError, TypeError):
            text = ""
    if not text:
        return None
    chat_id = str(message.get("chat_id") or "").strip()
    message_id = str(message.get("message_id") or "").strip()
    if not chat_id or not message_id:
        return None
    return FeishuImInbound(text=text, open_id=open_id, chat_id=chat_id, message_id=message_id)


def _is_allowed_feishu_sender(open_id: str, allow_from: list[str]) -> bool:
    if not allow_from:
        return False
    if "*" in allow_from:
        return True
    return open_id in allow_from


def _session_key_feishu(open_id: str) -> str:
    safe = open_id.replace("/", "_")
    return f"feishu_p2p_{safe}"


class FeishuChannelAdapter(ChannelAdapter):
    """Lark WS client (background thread) + Gateway turns + HTTP send with tenant token."""

    def __init__(
        self,
        gateway: Gateway,
        session_store: SessionStore,
        *,
        feishu: FeishuChannelSettings,
    ) -> None:
        super().__init__(gateway)
        self._store = session_store
        self._cfg = feishu
        self._http: httpx.AsyncClient | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending_assistant_text: dict[str, str] = {}
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    def _domain(self) -> str:
        return (self._cfg.domain or "https://open.feishu.cn").rstrip("/")

    async def _ensure_tenant_token(self) -> str:
        assert self._http is not None
        now = time.monotonic()
        if self._token and now < self._token_expires_at - 120:
            return self._token
        async with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at - 120:
                return self._token
            app_id = (self._cfg.app_id or "").strip()
            app_secret = (self._cfg.app_secret or "").strip()
            if not app_id or not app_secret:
                raise RuntimeError("feishu app_id / app_secret missing")
            url = f"{self._domain()}{_TENANT_TOKEN_PATH}"
            r = await self._http.post(
                url,
                json={"app_id": app_id, "app_secret": app_secret},
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            r.raise_for_status()
            body = r.json()
            if int(body.get("code", -1)) != 0:
                raise RuntimeError(f"tenant_access_token error: {body}")
            token = str(body.get("tenant_access_token") or "").strip()
            if not token:
                raise RuntimeError("empty tenant_access_token")
            expire = int(body.get("expire", 7200))
            self._token = token
            self._token_expires_at = time.monotonic() + max(60, expire)
            return token

    async def _send_text_open_id(self, open_id: str, text: str) -> None:
        assert self._http is not None
        token = await self._ensure_tenant_token()
        url = f"{self._domain()}{_SEND_MESSAGE_PATH}"
        payload = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        r = await self._http.post(
            url,
            params={"receive_id_type": "open_id"},
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        if r.status_code >= 400:
            logger.error("Feishu send HTTP %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        body = r.json()
        if int(body.get("code", -1)) != 0:
            logger.error("Feishu send API code=%s msg=%s", body.get("code"), body.get("msg"))

    async def _send_text_chunks(self, open_id: str, text: str) -> None:
        for chunk in _split_chunks(text.strip(), 18000):
            if chunk:
                await self._send_text_open_id(open_id, chunk)

    def _lark_thread_main(self) -> None:
        """Own event loop + **first** import of ``lark_oapi.ws.client`` in this thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import lark_oapi as lark  # noqa: PLC0415
            from lark_oapi.event.dispatcher_handler import (  # noqa: PLC0415
                EventDispatcherHandler,
            )
            from lark_oapi.ws.client import Client as LarkWsClient  # noqa: PLC0415
        except ImportError as exc:
            logger.error("Feishu: install dependency lark-oapi (%s)", exc)
            return

        def _on_p2_im(data: Any) -> None:
            try:
                parsed = _parse_im_message_receive_v1(data)
                if parsed is None:
                    return
                if not _is_allowed_feishu_sender(parsed.open_id, self._cfg.allow_from):
                    logger.info("Feishu: ignoring disallowed sender %s", parsed.open_id)
                    return
                ml = self._main_loop
                if ml is None or ml.is_closed():
                    return
                fut = asyncio.run_coroutine_threadsafe(
                    self._on_inbound_parsed(parsed),
                    ml,
                )

                def _done(f: asyncio.Future[Any]) -> None:
                    try:
                        f.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("Feishu gateway turn failed")

                fut.add_done_callback(_done)
            except Exception:
                logger.exception("Feishu: handler error")

        enc = (self._cfg.encrypt_key or "").strip()
        ver = (self._cfg.verification_token or "").strip()
        handler = (
            EventDispatcherHandler.builder(enc, ver)
            .register_p2_im_message_receive_v1(_on_p2_im)
            .build()
        )
        tag = (self._cfg.route_tag or "").strip()
        extra = ["edu-feishu"] + ([tag] if tag else [])
        app_id = (self._cfg.app_id or "").strip()
        app_secret = (self._cfg.app_secret or "").strip()
        try:
            client = LarkWsClient(
                app_id,
                app_secret,
                log_level=lark.LogLevel.INFO,
                event_handler=handler,
                domain=self._domain(),
                extra_ua_tags=extra,
            )
        except TypeError:
            client = LarkWsClient(
                app_id,
                app_secret,
                log_level=lark.LogLevel.INFO,
                event_handler=handler,
                domain=self._domain(),
            )
        try:
            client.start()
        except Exception:
            logger.exception("Feishu: WebSocket client exited")

    async def _on_inbound_parsed(self, parsed: FeishuImInbound) -> None:
        self._pending_assistant_text.pop(parsed.open_id, None)
        sid = _session_key_feishu(parsed.open_id)
        self._store.get_or_create_session_by_id(sid, user_id=parsed.open_id)
        inbound = InboundMessage.user_text(
            channel=ChannelKind.FEISHU,
            session_id=sid,
            user_id=parsed.open_id,
            content=parsed.text,
            metadata={
                "feishu_chat_id": parsed.chat_id,
                "feishu_message_id": parsed.message_id,
            },
        )
        auth = AuthContext(user_id=parsed.open_id, channel="feishu", api_key=None)
        try:
            async for ob in self.gateway.process_inbound_message(inbound, auth):
                await self._emit_outbound(ob, chat_open_id=parsed.open_id)
        finally:
            self._pending_assistant_text.pop(parsed.open_id, None)

    def _append_pending_assistant_text(self, open_id: str, fragment: str) -> None:
        self._pending_assistant_text[open_id] = self._pending_assistant_text.get(open_id, "") + fragment

    async def _flush_pending_assistant_text(self, open_id: str) -> None:
        buf = self._pending_assistant_text.pop(open_id, "") or ""
        body = buf.strip()
        if not body:
            return
        try:
            await self._send_text_chunks(open_id, body)
        except Exception:
            logger.exception("Feishu send failed")

    async def _emit_outbound(self, ob: OutboundMessage, *, chat_open_id: str) -> None:
        if ob.content_type == OutboundContentType.META:
            return
        if ob.content_type == OutboundContentType.TOOL_RESULT:
            return
        if ob.content_type == OutboundContentType.TOOL_CALL:
            await self._flush_pending_assistant_text(chat_open_id)
            return
        if ob.content_type == OutboundContentType.TEXT:
            if not ob.is_final:
                self._append_pending_assistant_text(chat_open_id, ob.content or "")
                return
            self._pending_assistant_text.pop(chat_open_id, None)
            body = (ob.content or "").strip()
            if not body:
                return
            try:
                await self._send_text_chunks(chat_open_id, body)
            except Exception:
                logger.exception("Feishu send failed")
            return
        if ob.content_type == OutboundContentType.ERROR:
            await self._flush_pending_assistant_text(chat_open_id)
            body = (ob.content or "").strip()
            if not body:
                return
            try:
                await self._send_text_chunks(chat_open_id, body)
            except Exception:
                logger.exception("Feishu send failed")

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=60.0)
        self._main_loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._lark_thread_main,
            name="edu-feishu-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info("Feishu channel: background WS thread started")

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        # Lark ``ws.Client`` has no public stop; thread may block until process exit.
        self._thread = None


def _split_chunks(text: str, max_len: int) -> list[str]:
    t = text.strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    return [t[i : i + max_len] for i in range(0, len(t), max_len)]


def feishu_channel_ready(cfg: FeishuChannelSettings) -> bool:
    """True when minimal credentials and allowlist are present."""
    if not cfg.enabled:
        return False
    if not (cfg.app_id or "").strip() or not (cfg.app_secret or "").strip():
        return False
    if not cfg.allow_from:
        logger.warning("Feishu enabled but allow_from is empty — refusing to start (set ['*'] or open_ids)")
        return False
    return True
