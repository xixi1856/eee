"""Unit tests for Feishu IM parsing and allowlist (no live WebSocket)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from edu_agent.channels.feishu import (
    FeishuChannelSettings,
    _is_allowed_feishu_sender,
    _parse_im_message_receive_v1,
    _session_key_feishu,
    feishu_channel_ready,
)


def test_parse_im_p2p_text_object_shape() -> None:
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_type="text",
                chat_type="p2p",
                chat_id="oc_1",
                message_id="om_1",
                content='{"text":"hello"}',
            ),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou_abc"),
            ),
        )
    )
    out = _parse_im_message_receive_v1(data)
    assert out is not None
    assert out.text == "hello"
    assert out.open_id == "ou_abc"
    assert out.chat_id == "oc_1"
    assert out.message_id == "om_1"


def test_parse_im_skips_group_chat() -> None:
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_type="text",
                chat_type="group",
                chat_id="oc_g",
                message_id="om_g",
                content='{"text":"hi"}',
            ),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou_x"),
            ),
        )
    )
    assert _parse_im_message_receive_v1(data) is None


def test_parse_im_skips_non_text() -> None:
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_type="image",
                chat_type="p2p",
                chat_id="oc_1",
                message_id="om_1",
                content="{}",
            ),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou_x"),
            ),
        )
    )
    assert _parse_im_message_receive_v1(data) is None


def test_allow_from_star() -> None:
    assert _is_allowed_feishu_sender("ou_any", ["*"])
    assert _is_allowed_feishu_sender("ou_1", ["ou_1", "ou_2"])
    assert not _is_allowed_feishu_sender("ou_3", ["ou_1", "ou_2"])


def test_session_key_stable() -> None:
    assert _session_key_feishu("ou/a") == "feishu_p2p_ou_a"


@pytest.mark.parametrize(
    ("cfg", "ready"),
    [
        (FeishuChannelSettings(enabled=False, app_id="x", app_secret="y", allow_from=["*"]), False),
        (FeishuChannelSettings(enabled=True, app_id="", app_secret="y", allow_from=["*"]), False),
        (FeishuChannelSettings(enabled=True, app_id="x", app_secret="", allow_from=["*"]), False),
        (FeishuChannelSettings(enabled=True, app_id="x", app_secret="y", allow_from=[]), False),
        (FeishuChannelSettings(enabled=True, app_id="x", app_secret="y", allow_from=["*"]), True),
    ],
)
def test_feishu_channel_ready(cfg: FeishuChannelSettings, ready: bool) -> None:
    assert feishu_channel_ready(cfg) is ready
