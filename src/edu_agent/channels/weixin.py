"""Personal WeChat (ilinkai HTTP long-poll) — aligned with HKUDS nanobot weixin channel.

Protocol and defaults follow ``nanobot/channels/weixin.py`` (ilinkai.weixin.qq.com).
No user ``base_url`` is required unless you override the default gateway.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType, OutboundMessage
from edu_agent.channels.base import ChannelAdapter
from edu_agent.config import EduSettings, WeixinChannelSettings
from edu_agent.paths import EduPaths
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore

logger = logging.getLogger(__name__)

# --- Protocol constants (nanobot / openclaw-weixin) ---
ITEM_TEXT = 1
MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
WEIXIN_MAX_MESSAGE_LEN = 4000
WEIXIN_CHANNEL_VERSION = "2.1.1"
ILINK_APP_ID = "bot"
ERRCODE_SESSION_EXPIRED = -14
SESSION_PAUSE_DURATION_S = 60 * 60
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30.0
RETRY_DELAY_S = 2.0
MAX_QR_REFRESH_COUNT = 3
BASE_INFO: dict[str, str] = {"channel_version": WEIXIN_CHANNEL_VERSION}


def _build_client_version(version: str) -> int:
    parts = version.split(".")

    def _as_int(idx: int) -> int:
        try:
            return int(parts[idx])
        except Exception:
            return 0

    major = _as_int(0)
    minor = _as_int(1)
    patch = _as_int(2)
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(WEIXIN_CHANNEL_VERSION)


def resolve_weixin_state_dir(settings: EduSettings, paths: EduPaths) -> Path:
    raw = (settings.runtime.channels.weixin.state_dir or "").strip()
    if raw:
        p = Path(raw).expanduser()
        out = p.resolve() if p.is_absolute() else (paths.workspace / p).resolve()
    else:
        out = (paths.workspace / ".edu_agent" / "weixin").resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def account_json_has_token(state_dir: Path) -> bool:
    path = state_dir / "account.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(str(data.get("token") or "").strip())
    except Exception:
        return False


def _split_weixin_chunks(text: str, max_len: int = WEIXIN_MAX_MESSAGE_LEN) -> list[str]:
    t = text.strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    return [t[i : i + max_len] for i in range(0, len(t), max_len)]


def _print_scan_target(scan_url: str) -> None:
    """Print QR to terminal if ``qrcode`` is installed, else print URL / hint."""
    if scan_url.startswith("http"):
        click_hint = scan_url
    else:
        click_hint = "(raw QR payload — install `qrcode` for ASCII QR, or use WeChat scan from app)"
    try:
        import qrcode  # type: ignore[import-untyped]

        qr = qrcode.QRCode(border=1)
        qr.add_data(scan_url)
        qr.make(fit=True)
        print("\n--- WeChat login: scan QR below ---\n")
        qr.print_ascii(invert=True)
        print()
    except ImportError:
        print(f"\n--- WeChat login ---\n{click_hint}\n")


def _random_wechat_uin() -> str:
    uint32 = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(uint32).encode()).decode()


class WeixinIlinkClient:
    """HTTP client for ilinkai personal WeChat (long-poll + sendmessage)."""

    def __init__(self, config: WeixinChannelSettings, state_dir: Path) -> None:
        self.config = config
        self._state_dir = state_dir
        self._client: httpx.AsyncClient | None = None
        self._token = ""
        self._get_updates_buf = ""
        self._context_tokens: dict[str, str] = {}
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._next_poll_timeout_s = int(config.poll_timeout_sec)
        self._session_pause_until = 0.0
        self._running = False
        self._on_inbound: Callable[[str, str, str], Awaitable[None]] | None = None

    def _make_headers(self, *, auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-WECHAT-UIN": _random_wechat_uin(),
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        rt = str(self.config.route_tag or "").strip()
        if rt:
            headers["SKRouteTag"] = rt
        return headers

    async def _api_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        assert self._client is not None
        url = f"{self.config.base_url.rstrip('/')}/{endpoint}"
        resp = await self._client.get(url, params=params or {}, headers=self._make_headers(auth=auth))
        resp.raise_for_status()
        return resp.json()

    async def _api_get_with_base(
        self,
        *,
        base_url: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        assert self._client is not None
        url = f"{base_url.rstrip('/')}/{endpoint}"
        resp = await self._client.get(url, params=params or {}, headers=self._make_headers(auth=auth))
        resp.raise_for_status()
        return resp.json()

    async def _api_post(self, endpoint: str, body: dict[str, Any] | None, *, auth: bool = True) -> dict[str, Any]:
        assert self._client is not None
        url = f"{self.config.base_url.rstrip('/')}/{endpoint}"
        payload = dict(body or {})
        if "base_info" not in payload:
            payload["base_info"] = BASE_INFO
        resp = await self._client.post(url, json=payload, headers=self._make_headers(auth=auth))
        resp.raise_for_status()
        return resp.json()

    def _state_file(self) -> Path:
        return self._state_dir / "account.json"

    def _load_state(self) -> bool:
        path = self._state_file()
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._token = str(data.get("token") or "")
            self._get_updates_buf = str(data.get("get_updates_buf") or "")
            ctx = data.get("context_tokens")
            if isinstance(ctx, dict):
                self._context_tokens = {
                    str(k): str(v) for k, v in ctx.items() if str(k).strip() and str(v).strip()
                }
            bu = str(data.get("base_url") or "").strip()
            if bu:
                self.config.base_url = bu
            return bool(self._token)
        except Exception:
            logger.exception("failed to load Weixin account.json")
            return False

    def _save_state(self) -> None:
        path = self._state_file()
        with suppress(Exception):
            path.write_text(
                json.dumps(
                    {
                        "token": self._token,
                        "get_updates_buf": self._get_updates_buf,
                        "context_tokens": self._context_tokens,
                        "base_url": self.config.base_url,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def has_credentials(self) -> bool:
        if (self.config.token or "").strip():
            return True
        return self._load_state()

    def _is_allowed(self, from_user_id: str) -> bool:
        af = self.config.allow_from
        if not af or "*" in af:
            return True
        return from_user_id in af

    @staticmethod
    def _is_retryable_qr_poll_error(err: Exception) -> bool:
        if isinstance(err, httpx.TimeoutException | httpx.TransportError):
            return True
        if isinstance(err, httpx.HTTPStatusError):
            code = err.response.status_code if err.response is not None else 0
            return code >= 500
        return False

    async def _fetch_qr_code(self) -> tuple[str, str]:
        data = await self._api_get("ilink/bot/get_bot_qrcode", {"bot_type": "3"}, auth=False)
        qrcode_id = str(data.get("qrcode") or "")
        qrcode_img_content = str(data.get("qrcode_img_content") or "")
        if not qrcode_id:
            raise RuntimeError(f"get_bot_qrcode failed: {data}")
        scan = qrcode_img_content or qrcode_id
        return qrcode_id, scan

    async def _qr_login_loop(self) -> bool:
        refresh_count = 0
        qrcode_id, scan_url = await self._fetch_qr_code()
        _print_scan_target(scan_url)
        current_base = self.config.base_url.rstrip("/")

        while self._running:
            try:
                status_data = await self._api_get_with_base(
                    base_url=current_base,
                    endpoint="ilink/bot/get_qrcode_status",
                    params={"qrcode": qrcode_id},
                    auth=False,
                )
            except Exception as e:
                if self._is_retryable_qr_poll_error(e):
                    await asyncio.sleep(1)
                    continue
                raise

            if not isinstance(status_data, dict):
                await asyncio.sleep(1)
                continue

            status = str(status_data.get("status") or "")
            if status == "confirmed":
                token = str(status_data.get("bot_token") or "")
                base_url = str(status_data.get("baseurl") or "").strip()
                if token:
                    self._token = token
                    if base_url:
                        self.config.base_url = base_url
                    self._save_state()
                    logger.info("Weixin QR login successful")
                    return True
                logger.error("login confirmed but bot_token missing")
                return False
            if status == "scaned_but_redirect":
                redirect_host = str(status_data.get("redirect_host") or "").strip()
                if redirect_host:
                    if redirect_host.startswith("http://") or redirect_host.startswith("https://"):
                        current_base = redirect_host.rstrip("/")
                    else:
                        current_base = f"https://{redirect_host}".rstrip("/")
                await asyncio.sleep(1)
                continue
            if status == "expired":
                refresh_count += 1
                if refresh_count > MAX_QR_REFRESH_COUNT:
                    logger.warning("QR expired too many times, giving up")
                    return False
                qrcode_id, scan_url = await self._fetch_qr_code()
                current_base = self.config.base_url.rstrip("/")
                _print_scan_target(scan_url)
                continue
            await asyncio.sleep(1)
        return False

    async def login(self, *, force: bool) -> bool:
        """Interactive QR login; persists token to ``account.json``."""
        if force:
            self._token = ""
            self._get_updates_buf = ""
            self._context_tokens.clear()
            self.config.base_url = "https://ilinkai.weixin.qq.com"
            sf = self._state_file()
            if sf.exists():
                sf.unlink()
        if (self.config.token or "").strip():
            self._token = self.config.token.strip()
            self._save_state()
            return True
        if self._load_state():
            return True

        self._running = True
        timeout = httpx.Timeout(60.0, connect=30.0)
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        try:
            ok = await self._qr_login_loop()
            return ok
        finally:
            self._running = False
            if self._client:
                await self._client.aclose()
                self._client = None

    def _pause_session(self, duration_s: int = SESSION_PAUSE_DURATION_S) -> None:
        self._session_pause_until = time.time() + duration_s

    def _session_pause_remaining_s(self) -> int:
        remaining = int(self._session_pause_until - time.time())
        if remaining <= 0:
            self._session_pause_until = 0.0
            return 0
        return remaining

    async def _poll_once(self) -> None:
        remaining = self._session_pause_remaining_s()
        if remaining > 0:
            await asyncio.sleep(float(remaining))
            return

        assert self._client is not None
        self._client.timeout = httpx.Timeout(self._next_poll_timeout_s + 10, connect=30.0)
        body: dict[str, Any] = {"get_updates_buf": self._get_updates_buf, "base_info": BASE_INFO}
        data = await self._api_post("ilink/bot/getupdates", body, auth=True)

        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        is_error = (ret is not None and ret != 0) or (errcode is not None and errcode != 0)
        if is_error:
            if errcode == ERRCODE_SESSION_EXPIRED or ret == ERRCODE_SESSION_EXPIRED:
                self._pause_session()
                logger.warning("Weixin session expired (errcode %s); pausing poll", errcode)
                return
            raise RuntimeError(
                f"getupdates failed: ret={ret} errcode={errcode} errmsg={data.get('errmsg', '')}"
            )

        server_timeout_ms = data.get("longpolling_timeout_ms")
        if server_timeout_ms and int(server_timeout_ms) > 0:
            self._next_poll_timeout_s = max(int(server_timeout_ms) // 1000, 5)

        new_buf = data.get("get_updates_buf", "")
        if new_buf:
            self._get_updates_buf = str(new_buf)
            self._save_state()

        msgs: list[dict[str, Any]] = data.get("msgs") or []
        for msg in msgs:
            with suppress(Exception):
                await self._handle_inbound_dict(msg)

    def _extract_text(self, msg: dict[str, Any]) -> tuple[str, str, str]:
        """Returns (from_user_id, text, msg_id). Empty text means skip."""
        if msg.get("message_type") == MESSAGE_TYPE_BOT:
            return "", "", ""
        msg_id = str(msg.get("message_id") or msg.get("seq") or "")
        if not msg_id:
            msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
        from_user_id = str(msg.get("from_user_id") or "")
        if not from_user_id:
            return "", "", ""

        parts: list[str] = []
        for item in msg.get("item_list") or []:
            if item.get("type", 0) != ITEM_TEXT:
                continue
            text = (item.get("text_item") or {}).get("text", "")
            if text:
                parts.append(str(text))
        body = "\n".join(parts).strip()
        return from_user_id, body, msg_id

    async def _handle_inbound_dict(self, msg: dict[str, Any]) -> None:
        from_user_id, text, msg_id = self._extract_text(msg)
        if not from_user_id or not text:
            return
        if not self._is_allowed(from_user_id):
            logger.info("Weixin: ignoring disallowed sender %s", from_user_id)
            return
        if msg_id in self._processed_ids:
            return
        self._processed_ids[msg_id] = None
        while len(self._processed_ids) > 1000:
            self._processed_ids.popitem(last=False)

        ctx = str(msg.get("context_token") or "")
        if ctx:
            self._context_tokens[from_user_id] = ctx
            self._save_state()

        if self._on_inbound is None:
            return
        await self._on_inbound(from_user_id, text, msg_id)

    def set_inbound_handler(self, fn: Callable[[str, str, str], Awaitable[None]]) -> None:
        self._on_inbound = fn

    async def send_text_chunks(self, to_user_id: str, text: str) -> None:
        ctx = self._context_tokens.get(to_user_id, "")
        if not ctx:
            raise RuntimeError(f"missing context_token for WeChat user {to_user_id!r}")
        for chunk in _split_weixin_chunks(text):
            await self._send_text(to_user_id, chunk, ctx)

    async def _send_text(self, to_user_id: str, text: str, context_token: str) -> None:
        assert self._client is not None
        client_id = f"edu_agent-{uuid.uuid4().hex[:12]}"
        item_list: list[dict[str, Any]] = [{"type": ITEM_TEXT, "text_item": {"text": text}}]
        weixin_msg: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": MESSAGE_TYPE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": item_list,
            "context_token": context_token,
        }
        data = await self._api_post("ilink/bot/sendmessage", {"msg": weixin_msg}, auth=True)
        errcode = data.get("errcode", 0)
        if errcode not in (0, None):
            raise RuntimeError(f"sendmessage errcode={errcode} errmsg={data.get('errmsg', '')}")

    async def run_forever(self) -> None:
        """Long-poll loop until ``stop()``."""
        tok = (self.config.token or "").strip()
        if tok:
            self._token = tok
        elif not self._load_state():
            logger.error("Weixin: no token — run `uv run edu channels login weixin`")
            return

        self._running = True
        self._next_poll_timeout_s = int(self.config.poll_timeout_sec)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._next_poll_timeout_s + 10, connect=30.0),
            follow_redirects=True,
        )
        consecutive = 0
        try:
            while self._running:
                try:
                    await self._poll_once()
                    consecutive = 0
                except httpx.TimeoutException:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    if not self._running:
                        break
                    consecutive += 1
                    logger.exception("Weixin poll error (%s consecutive)", consecutive)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        consecutive = 0
                        await asyncio.sleep(BACKOFF_DELAY_S)
                    else:
                        await asyncio.sleep(self.config.poll_interval_sec)
        finally:
            self._running = False
            if self._client:
                await self._client.aclose()
                self._client = None
            self._save_state()

    def stop(self) -> None:
        self._running = False


class WeixinChannelAdapter(ChannelAdapter):
    """Long-poll ilinkai + Gateway turns (text in/out)."""

    def __init__(
        self,
        gateway: Gateway,
        session_store: SessionStore,
        *,
        weixin: WeixinChannelSettings,
        state_dir: Path,
    ) -> None:
        super().__init__(gateway)
        self._store = session_store
        self._state_dir = state_dir
        self._client = WeixinIlinkClient(weixin, state_dir)
        self._poll_task: asyncio.Task[None] | None = None
        # Stream TEXT deltas (is_final=False) buffered per WeChat user; flush on final, tool call, or error.
        self._pending_assistant_text: dict[str, str] = {}

    def _session_key(self, ilink_user_id: str) -> str:
        safe = ilink_user_id.replace("/", "_")
        return f"wx_ilink_{safe}"

    async def start(self) -> None:
        self._client.set_inbound_handler(self._on_weixin_text)
        self._poll_task = asyncio.create_task(self._client.run_forever(), name="edu-weixin-poll")

    async def _on_weixin_text(self, from_user_id: str, text: str, _msg_id: str) -> None:
        self._pending_assistant_text.pop(from_user_id, None)
        sid = self._session_key(from_user_id)
        self._store.get_or_create_session_by_id(sid, user_id=from_user_id)
        inbound = InboundMessage.user_text(
            channel=ChannelKind.WEIXIN,
            session_id=sid,
            user_id=from_user_id,
            content=text,
            metadata={},
        )
        auth = AuthContext(user_id=from_user_id, channel="weixin", api_key=None)
        try:
            async for ob in self.gateway.process_inbound_message(inbound, auth):
                await self._emit_outbound(ob, chat_id=from_user_id)
        except Exception:
            logger.exception("Weixin gateway turn failed")
        finally:
            self._pending_assistant_text.pop(from_user_id, None)

    def _append_pending_assistant_text(self, chat_id: str, fragment: str) -> None:
        self._pending_assistant_text[chat_id] = self._pending_assistant_text.get(chat_id, "") + fragment

    async def _flush_pending_assistant_text(self, chat_id: str) -> None:
        buf = self._pending_assistant_text.pop(chat_id, "") or ""
        body = buf.strip()
        if not body:
            return
        try:
            await self._client.send_text_chunks(chat_id, body)
        except Exception:
            logger.exception("Weixin send failed")

    async def _emit_outbound(self, ob: OutboundMessage, *, chat_id: str) -> None:
        if ob.content_type == OutboundContentType.META:
            return
        if ob.content_type == OutboundContentType.TOOL_RESULT:
            return
        if ob.content_type == OutboundContentType.TOOL_CALL:
            await self._flush_pending_assistant_text(chat_id)
            return
        if ob.content_type == OutboundContentType.TEXT:
            if not ob.is_final:
                self._append_pending_assistant_text(chat_id, ob.content or "")
                return
            self._pending_assistant_text.pop(chat_id, None)
            body = (ob.content or "").strip()
            if not body:
                return
            try:
                await self._client.send_text_chunks(chat_id, body)
            except Exception:
                logger.exception("Weixin send failed")
            return
        if ob.content_type == OutboundContentType.ERROR:
            await self._flush_pending_assistant_text(chat_id)
            body = (ob.content or "").strip()
            if not body:
                return
            try:
                await self._client.send_text_chunks(chat_id, body)
            except Exception:
                logger.exception("Weixin send failed")
            return

    async def stop(self) -> None:
        self._client.stop()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
        self._poll_task = None


async def run_weixin_qr_login(*, settings: EduSettings, paths: EduPaths, force: bool) -> bool:
    """CLI entry: QR login and persist ``account.json``."""
    wx = settings.runtime.channels.weixin
    sd = resolve_weixin_state_dir(settings, paths)
    client = WeixinIlinkClient(wx, sd)
    return await client.login(force=force)
