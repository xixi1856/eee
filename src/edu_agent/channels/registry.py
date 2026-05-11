"""Register ``ChannelAdapter`` instances from ``EduSettings`` (HTTP + optional IM)."""

from __future__ import annotations

import logging
from edu_agent.channels.feishu import FeishuChannelAdapter, feishu_channel_ready
from edu_agent.channels.http import HTTPChannelAdapter
from edu_agent.channels.weixin import (
    WeixinChannelAdapter,
    account_json_has_token,
    resolve_weixin_state_dir,
)
from edu_agent.config import EduSettings
from edu_agent.paths import EduPaths
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore

logger = logging.getLogger(__name__)


def register_channel_adapters(
    gateway: Gateway,
    *,
    settings: EduSettings,
    paths: EduPaths,
    session_store: SessionStore,
    host: str,
    port: int,
) -> None:
    """Register HTTP adapter first, then optional Weixin / Feishu per ``runtime.channels``."""
    http = HTTPChannelAdapter(
        gateway,
        session_store,
        host=host,
        port=port,
    )
    gateway.register_adapter(http)

    wx_cfg = settings.runtime.channels.weixin
    if wx_cfg.enabled:
        wx_state = resolve_weixin_state_dir(settings, paths)
        if (wx_cfg.token or "").strip() or account_json_has_token(wx_state):
            gateway.register_adapter(
                WeixinChannelAdapter(
                    gateway,
                    session_store,
                    weixin=wx_cfg,
                    state_dir=wx_state,
                )
            )
            logger.info("Weixin channel enabled (ilinkai long-poll, default base_url)")
        else:
            logger.warning(
                "Weixin enabled but no token — run `uv run edu channels login weixin` "
                "or set runtime.channels.weixin.token in edu_agent.yaml"
            )

    fs_cfg = settings.runtime.channels.feishu
    if feishu_channel_ready(fs_cfg):
        gateway.register_adapter(
            FeishuChannelAdapter(
                gateway,
                session_store,
                feishu=fs_cfg,
            )
        )
        logger.info("Feishu channel enabled (lark-oapi WebSocket, p2p text)")
    elif fs_cfg.enabled:
        logger.warning(
            "Feishu enabled but missing app_id/app_secret or allow_from — skipping adapter"
        )
