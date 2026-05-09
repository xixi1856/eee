"""CLI channel — stdin/stdout only; all turns go through ``Gateway``."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import click

from edu_agent.auth.models import AuthContext
from edu_agent.bus.models import ChannelKind, InboundMessage, OutboundContentType
from edu_agent.channels.base import ChannelAdapter
from edu_agent.runner.gateway import Gateway

logger = logging.getLogger(__name__)


class CLIChannelAdapter(ChannelAdapter):
    """Interactive CLI; optional no-op ``start``/``stop`` for in-process use."""

    def __init__(self, gateway: Gateway) -> None:
        super().__init__(gateway)

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def run_chat_loop(
        self,
        *,
        user_id: str,
        initial_session_id: str,
        get_progress_mode: Callable[[], str],
        on_mode_cycle: Callable[[], None],
        gateway_mode_label: bool = False,
    ) -> None:
        """Read lines from stdin; print assistant output from outbound stream."""
        session_id = initial_session_id
        click.echo(
            click.style(
                "EduAgent（Gateway 模式）。" if gateway_mode_label else "EduAgent。",
                fg="green",
            )
            + " /quit /exit 退出；/reset 新会话；/verbose 切换进度；/compress-context 压缩上下文。"
        )
        click.echo(click.style(f"会话 ID: {session_id}", dim=True))

        while True:
            try:
                user_input = click.prompt(click.style("你", fg="cyan"), prompt_suffix=" > ")
            except (EOFError, KeyboardInterrupt):
                click.echo("\n再见！")
                break

            stripped = user_input.strip()
            if not stripped:
                continue
            if stripped in ("/quit", "/exit", "quit", "exit"):
                click.echo("再见！")
                break
            if stripped == "/reset":
                auth = AuthContext(user_id=user_id, channel="cli")
                try:
                    session_id = await self.gateway.cli_abandon_session(session_id, auth)
                except Exception as exc:  # noqa: BLE001
                    click.echo(click.style(f"[reset 失败] {exc}", fg="red"), err=True)
                    continue
                click.echo(click.style(f"[新会话] {session_id}", dim=True))
                continue
            if stripped == "/verbose":
                on_mode_cycle()
                continue
            if stripped in ("/compress-context", "/ctx-compress"):
                auth = AuthContext(user_id=user_id, channel="cli")
                try:
                    ok = await self.gateway.cli_compress_context(session_id, auth)
                    if ok:
                        click.echo(click.style("[上下文压缩已触发]", dim=True))
                    else:
                        click.echo(
                            click.style(
                                "[提示] 尚无活动 runner；请先发送一条消息再压缩。",
                                dim=True,
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    click.echo(click.style(f"[压缩失败] {exc}", fg="red"), err=True)
                continue

            auth = AuthContext(user_id=user_id, channel="cli")
            inbound = InboundMessage.user_text(
                channel=ChannelKind.CLI,
                session_id=session_id,
                user_id=user_id,
                content=stripped,
                metadata={"cli_progress": get_progress_mode()},
            )
            try:
                printed_header = False
                async for ob in self.gateway.process_inbound_message(inbound, auth):
                    if ob.content_type == OutboundContentType.ERROR:
                        click.echo(click.style(f"[错误] {ob.content}", fg="red"), err=True)
                        printed_header = True
                        break
                    if ob.content_type == OutboundContentType.TEXT:
                        if not ob.is_final:
                            if not printed_header:
                                click.echo(click.style("助手", fg="yellow") + " > ", nl=False)
                                printed_header = True
                            click.echo(ob.content, nl=False)
                        else:
                            if printed_header:
                                click.echo()
                            else:
                                click.echo(click.style("助手", fg="yellow") + " > " + (ob.content or ""))
                    elif ob.content_type == OutboundContentType.TOOL_CALL:
                        try:
                            tc = json.loads(ob.content)
                            name = tc.get("name", "?")
                        except json.JSONDecodeError:
                            name = "?"
                        click.echo(click.style(f"  [工具] {name}", dim=True))
                    elif ob.content_type == OutboundContentType.TOOL_RESULT:
                        click.echo(click.style("  [工具完成]", dim=True))
                    elif ob.content_type == OutboundContentType.META:
                        click.echo(click.style(f"  [meta] {ob.content}", dim=True))
                click.echo()
            except Exception as exc:  # noqa: BLE001
                logger.exception("CLI chat error: %s", exc)
                click.echo(click.style(f"[错误] {exc}", fg="red"), err=True)
