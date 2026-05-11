"""FastAPI app factory — HTTP routes only call Gateway / SessionStore."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from edu_agent.auth.checker import AuthorizationError
from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore
from edu_agent.toolsets.registry import discover_builtin_tools, toolset_registry

logger = logging.getLogger(__name__)


class CreateSessionBody(BaseModel):
    user_id: str = Field(default="default")
    title: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionBody(BaseModel):
    model: str = ""
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False


def _verify_or_401(gateway: Gateway, auth: AuthContext) -> None:
    try:
        gateway.verify_http_optional(auth)
    except AuthorizationError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc


def _auth_from_request(
    request: Request,
    *,
    user_id: str = "default",
    x_api_key: str | None = None,
) -> AuthContext:
    auth_header = request.headers.get("authorization") or ""
    api_key = x_api_key
    if auth_header.lower().startswith("bearer "):
        api_key = auth_header[7:].strip() or api_key
    return AuthContext(
        user_id=user_id,
        channel="http",
        api_key=api_key,
    )


def create_app(
    gateway: Gateway,
    *,
    session_store: SessionStore,
) -> FastAPI:
    discover_builtin_tools()
    app = FastAPI(title="EduAgent API", version="1.0.0")

    def gw_dep() -> Gateway:
        return gateway

    def store_dep() -> SessionStore:
        return session_store

    @app.post("/v1/sessions")
    async def create_session(
        request: Request,
        payload: CreateSessionBody = Body(...),
        store: SessionStore = Depends(store_dep),
        gw: Gateway = Depends(gw_dep),
    ) -> dict[str, Any]:
        auth = _auth_from_request(request, user_id=payload.user_id)
        _verify_or_401(gw, auth)
        sess = store.create_session(payload.user_id)
        return {"id": sess.metadata.id, "user_id": sess.metadata.user_id, "status": sess.metadata.status.value}

    @app.get("/v1/sessions/{session_id}")
    async def get_session(
        session_id: str,
        request: Request,
        store: SessionStore = Depends(store_dep),
        gw: Gateway = Depends(gw_dep),
    ) -> dict[str, Any]:
        auth = _auth_from_request(request, user_id=request.query_params.get("user_id", "default"))
        _verify_or_401(gw, auth)
        sess = store.get_session(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            gw.ensure_session_owner(auth, session_user_id=sess.metadata.user_id)
        except AuthorizationError:
            raise HTTPException(status_code=403, detail="forbidden") from None
        return {
            "id": sess.metadata.id,
            "user_id": sess.metadata.user_id,
            "status": sess.metadata.status.value,
            "created_at": sess.metadata.created_at.isoformat(),
            "updated_at": sess.metadata.updated_at.isoformat(),
        }

    @app.get("/v1/sessions")
    async def list_sessions(
        request: Request,
        store: SessionStore = Depends(store_dep),
        gw: Gateway = Depends(gw_dep),
        user_id: str = Query(default="default"),
        limit: int = Query(default=20, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        auth = _auth_from_request(request, user_id=user_id)
        _verify_or_401(gw, auth)
        sessions = store.search_sessions(user_id=user_id, limit=limit)
        return [
            {
                "id": s.metadata.id,
                "user_id": s.metadata.user_id,
                "status": s.metadata.status.value,
                "updated_at": s.metadata.updated_at.isoformat(),
                "title": s.metadata.title,
            }
            for s in sessions
        ]

    @app.get("/v1/tools")
    async def list_tools(request: Request, gw: Gateway = Depends(gw_dep)) -> list[dict[str, Any]]:
        auth = _auth_from_request(request)
        _verify_or_401(gw, auth)
        specs = toolset_registry.list_specs(gw.settings)
        return [
            {
                "name": s.name,
                "toolset": s.toolset,
                "description": s.description,
                "permissions": [p.value for p in s.permissions],
                "approval_required": s.approval_required,
            }
            for s in specs
        ]

    def _last_user_text(messages: list[ChatMessage]) -> str:
        for m in reversed(messages):
            if m.role == "user" and m.content:
                return m.content
        return ""

    def _platform_chat_metadata(request: Request, *, user_id: str, auth_api_key: str | None) -> dict[str, Any]:
        """Headers from Next.js / platform (phase8).

        ``X-Platform-User-Id`` when set must equal ``user_id`` (the Agent session user id
        from the query string), not the platform UUID.
        """
        h = request.headers
        platform_uid = (h.get("x-platform-user-id") or "").strip()
        if platform_uid and platform_uid != user_id:
            raise HTTPException(
                status_code=400,
                detail="X-Platform-User-Id must match user_id query parameter (agent user id)",
            )
        course = (h.get("x-platform-course-id") or "").strip()
        lesson = (h.get("x-platform-lesson-id") or "").strip()
        meta: dict[str, Any] = {"api_key": auth_api_key}
        if course:
            meta["platform_course_id"] = course
        if lesson:
            meta["platform_lesson_id"] = lesson
        return meta

    def _sse_line(obj: Any) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: Request,
        payload: ChatCompletionBody = Body(...),
        gw: Gateway = Depends(gw_dep),
        session_id: str = Query(..., description="EduAgent session id"),
        user_id: str = Query(default="default"),
    ) -> StreamingResponse | JSONResponse:
        auth = _auth_from_request(request, user_id=user_id)
        _verify_or_401(gw, auth)
        text = _last_user_text(payload.messages)
        if not text.strip():
            raise HTTPException(status_code=400, detail="no user message")

        inbound = InboundMessage.user_text(
            channel=ChannelKind.HTTP,
            session_id=session_id,
            user_id=user_id,
            content=text.strip(),
            metadata=_platform_chat_metadata(request, user_id=user_id, auth_api_key=auth.api_key),
        )

        if payload.stream:

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async for ob in gw.process_inbound_message(inbound, auth):
                        if await request.is_disconnected():
                            break
                        frame: dict[str, Any] = {
                            "id": str(ob.message_id),
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop" if ob.is_final else None,
                                }
                            ],
                        }
                        ch0 = frame["choices"][0]
                        if ob.content_type == OutboundContentType.TEXT:
                            ch0["delta"] = {"content": ob.content}
                        elif ob.content_type == OutboundContentType.TOOL_CALL:
                            try:
                                tc = json.loads(ob.content)
                                ch0["delta"] = {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": tc.get("id", ""),
                                            "type": "function",
                                            "function": {
                                                "name": tc.get("name", ""),
                                                "arguments": tc.get("arguments", ""),
                                            },
                                        }
                                    ]
                                }
                            except json.JSONDecodeError:
                                ch0["delta"] = {"content": ob.content}
                        elif ob.content_type == OutboundContentType.TOOL_RESULT:
                            ch0["delta"] = {"role": "tool", "content": ob.content}
                        elif ob.content_type == OutboundContentType.ERROR:
                            ch0["delta"] = {"content": ob.content}
                            ch0["finish_reason"] = "stop"
                        elif ob.content_type == OutboundContentType.META:
                            ch0["delta"] = {"content": ""}
                        edu_meta: dict[str, Any] = {
                            "content_type": ob.content_type.value,
                            "is_final": ob.is_final,
                        }
                        if ob.metadata:
                            edu_meta["b3"] = ob.metadata
                        frame["edu_meta"] = edu_meta
                        yield _sse_line(frame).encode("utf-8")
                    yield b"data: [DONE]\n\n"
                except Exception as exc:  # noqa: BLE001
                    err = {"error": str(exc), "choices": []}
                    yield _sse_line(err).encode("utf-8")

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        # non-stream: aggregate final TEXT
        final_text = ""
        async for ob in gw.process_inbound_message(inbound, auth):
            if ob.content_type == OutboundContentType.TEXT and ob.is_final:
                final_text = ob.content or ""
        return JSONResponse(
            {
                "id": str(inbound.message_id),
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": final_text},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    @app.websocket("/v1/ws")
    async def ws_chat(ws: WebSocket, gw: Gateway = Depends(gw_dep)) -> None:
        from edu_agent.channels.websocket import websocket_chat_loop

        await websocket_chat_loop(ws, gw)

    return app
