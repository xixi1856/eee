"""WebSocket transport — forwards JSON frames via Gateway only."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType
from edu_agent.runner.gateway import Gateway

logger = logging.getLogger(__name__)


async def websocket_chat_loop(ws: WebSocket, gateway: Gateway) -> None:
    await ws.accept()
    session_id = ws.query_params.get("session_id") or ""
    user_id = ws.query_params.get("user_id") or "default"
    api_key = ws.query_params.get("api_key") or ""
    if not session_id.strip():
        await ws.close(code=4400)
        return
    auth = AuthContext(user_id=user_id, channel="websocket", api_key=api_key or None)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "invalid json"}, ensure_ascii=False))
                continue
            content = (payload.get("content") or "").strip()
            if not content:
                continue
            inbound = InboundMessage.user_text(
                channel=ChannelKind.WEBSOCKET,
                session_id=session_id.strip(),
                user_id=user_id,
                content=content,
                metadata={"api_key": api_key} if api_key else {},
            )
            try:
                async for ob in gateway.process_inbound_message(inbound, auth):
                    await ws.send_text(
                        json.dumps(
                            {
                                "content": ob.content,
                                "content_type": ob.content_type.value,
                                "is_final": ob.is_final,
                            },
                            ensure_ascii=False,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("ws turn failed: %s", exc)
                await ws.send_text(
                    json.dumps(
                        {"error": str(exc), "content_type": OutboundContentType.ERROR.value},
                        ensure_ascii=False,
                    )
                )
    except WebSocketDisconnect:
        pass
