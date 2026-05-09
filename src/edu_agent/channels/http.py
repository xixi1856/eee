"""HTTP(S) server adapter — runs uvicorn + FastAPI app from ``create_app``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import uvicorn

from edu_agent.api.server import create_app
from edu_agent.channels.base import ChannelAdapter
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore

logger = logging.getLogger(__name__)


class HTTPChannelAdapter(ChannelAdapter):
    """Binds FastAPI to this process (transport only — routes use Gateway)."""

    def __init__(
        self,
        gateway: Gateway,
        session_store: SessionStore,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        super().__init__(gateway)
        self._session_store = session_store
        self._host = host
        self._port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        app = create_app(self.gateway, session_store=self._session_store)
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve(), name="edu-http-adapter")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        self._server = None
