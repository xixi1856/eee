"""Channel adapter base — transport only (no Agent orchestration)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import InboundMessage, OutboundMessage
from edu_agent.runner.gateway import Gateway


class ChannelAdapter(ABC):
    """Adapters convert wire format ↔ ``InboundMessage`` / ``OutboundMessage``."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> Gateway:
        return self._gateway

    @abstractmethod
    async def start(self) -> None:
        """Bind listeners / start subprocess (optional no-op for in-process CLI)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release transport resources."""

    async def send_inbound(
        self,
        inbound: InboundMessage,
    ) -> AsyncIterator[OutboundMessage]:
        """Default path: all channels forward to Gateway (no Agent import)."""
        auth = AuthContext(
            user_id=inbound.user_id,
            channel=inbound.channel.value,
            api_key=inbound.metadata.get("api_key"),
        )
        async for ob in self._gateway.process_inbound_message(inbound, auth):
            yield ob
