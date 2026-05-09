"""Weixin ilink client helpers and session mapping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from edu_agent.bus.models import OutboundContentType, OutboundMessage, new_message_id
from edu_agent.channels.weixin import (
    WeixinChannelAdapter,
    WeixinIlinkClient,
    _split_weixin_chunks,
    resolve_weixin_state_dir,
)
from edu_agent.config import AgentDefaults, EduSettings, WeixinChannelSettings
from edu_agent.paths import build_paths
from edu_agent.sessions.store import SessionStore


def test_split_weixin_chunks() -> None:
    long = "a" * 4500
    parts = _split_weixin_chunks(long, max_len=4000)
    assert len(parts) == 2
    assert parts[0] == "a" * 4000
    assert parts[1] == "a" * 500


def test_allow_from_star_and_explicit(tmp_path: Path) -> None:
    open_cfg = WeixinChannelSettings(allow_from=["*"])
    c_open = WeixinIlinkClient(open_cfg, tmp_path)
    assert c_open._is_allowed("anyone")

    closed = WeixinIlinkClient(WeixinChannelSettings(allow_from=["u1", "u2"]), tmp_path)
    assert closed._is_allowed("u1")
    assert not closed._is_allowed("u3")


def test_extract_text_basic(tmp_path: Path) -> None:
    c = WeixinIlinkClient(WeixinChannelSettings(), tmp_path)
    uid, text, mid = c._extract_text(
        {
            "message_type": 1,
            "message_id": "m1",
            "from_user_id": "wxuser",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
    )
    assert uid == "wxuser"
    assert text == "hello"
    assert mid == "m1"


def test_get_or_create_session_by_id(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    s1 = store.get_or_create_session_by_id("wx_ilink_abc", "abc")
    assert s1.metadata.id == "wx_ilink_abc"
    s2 = store.get_or_create_session_by_id("wx_ilink_abc", "abc")
    assert s2.metadata.id == "wx_ilink_abc"
    store.close()


def test_resolve_weixin_state_dir_default(tmp_path: Path) -> None:
    settings = EduSettings(agent=AgentDefaults(workspace=tmp_path))
    paths = build_paths(settings)
    d = resolve_weixin_state_dir(settings, paths)
    assert d.name == "weixin"
    assert d.parent.name == ".edu_agent"
    assert d.is_dir()


def _ob_text(
    *,
    content: str,
    is_final: bool,
    session_id: str = "wx_ilink_u1",
    user_id: str = "u1",
    in_reply_to=None,
) -> OutboundMessage:
    return OutboundMessage(
        in_reply_to=in_reply_to or new_message_id(),
        session_id=session_id,
        user_id=user_id,
        content=content,
        content_type=OutboundContentType.TEXT,
        is_final=is_final,
    )


@pytest.mark.asyncio
async def test_weixin_adapter_coalesces_stream_deltas_to_single_send(tmp_path: Path) -> None:
    gateway = MagicMock()
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    wx = WeixinChannelSettings()
    adapter = WeixinChannelAdapter(gateway, store, weixin=wx, state_dir=tmp_path)
    mock_send = AsyncMock()
    adapter._client.send_text_chunks = mock_send

    rid = new_message_id()
    chat = "wxuser1"
    await adapter._emit_outbound(
        _ob_text(content="hel", is_final=False, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )
    await adapter._emit_outbound(
        _ob_text(content="lo", is_final=False, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )
    await adapter._emit_outbound(
        _ob_text(content="hello", is_final=True, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )

    mock_send.assert_awaited_once()
    assert mock_send.await_args_list[0].args == (chat, "hello")
    store.close()


@pytest.mark.asyncio
async def test_weixin_adapter_flushes_pending_before_tool_call(tmp_path: Path) -> None:
    gateway = MagicMock()
    store = SessionStore(tmp_path / "sessions.db")
    wx = WeixinChannelSettings()
    adapter = WeixinChannelAdapter(gateway, store, weixin=wx, state_dir=tmp_path)
    mock_send = AsyncMock()
    adapter._client.send_text_chunks = mock_send

    rid = new_message_id()
    chat = "u2"
    await adapter._emit_outbound(
        _ob_text(content="Checking", is_final=False, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )
    await adapter._emit_outbound(
        _ob_text(content="…", is_final=False, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )
    tool_ob = OutboundMessage(
        in_reply_to=rid,
        session_id="s",
        user_id=chat,
        content='{"id":"1","name":"x","arguments":"{}"}',
        content_type=OutboundContentType.TOOL_CALL,
        is_final=False,
    )
    await adapter._emit_outbound(tool_ob, chat_id=chat)

    assert mock_send.await_count == 1
    assert mock_send.await_args_list[0].args == (chat, "Checking…")
    store.close()


@pytest.mark.asyncio
async def test_weixin_adapter_error_flushes_pending_then_error(tmp_path: Path) -> None:
    gateway = MagicMock()
    store = SessionStore(tmp_path / "sessions.db")
    wx = WeixinChannelSettings()
    adapter = WeixinChannelAdapter(gateway, store, weixin=wx, state_dir=tmp_path)
    mock_send = AsyncMock()
    adapter._client.send_text_chunks = mock_send

    rid = new_message_id()
    chat = "u3"
    await adapter._emit_outbound(
        _ob_text(content="partial", is_final=False, in_reply_to=rid, session_id="s", user_id=chat),
        chat_id=chat,
    )
    err = OutboundMessage(
        in_reply_to=rid,
        session_id="s",
        user_id=chat,
        content="boom",
        content_type=OutboundContentType.ERROR,
        is_final=True,
    )
    await adapter._emit_outbound(err, chat_id=chat)

    assert mock_send.await_count == 2
    assert mock_send.await_args_list[0].args == (chat, "partial")
    assert mock_send.await_args_list[1].args == (chat, "boom")
    store.close()
