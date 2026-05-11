"""CLI entry point for the educational agent.

Usage:
    edu chat [--user USER] [--skills SKILLS_DIR]
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import sys
import threading
import time

import click

from edu_agent.agent import EduAgent
from edu_agent.auth.checker import AuthorizationChecker
from edu_agent.channels import CLIChannelAdapter
from edu_agent.channels.weixin import run_weixin_qr_login
from edu_agent.config_loader import load_settings
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.paths import build_paths
from edu_agent.runner.gateway import Gateway
from edu_agent.sessions.store import SessionStore
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.types import AgentCallbacks, AgentConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool emoji mapping
# ---------------------------------------------------------------------------

_TOOL_EMOJIS: dict[str, str] = {
    "knowledge_query": "🔍",
    "generate_quiz": "📝",
    "build_mindmap": "🗺️",
    "parse_document": "📄",
    "ingest_document": "📥",
    "hint_generator": "💡",
    "score_essay": "✅",
    "evaluate_code": "💻",
    "delegate_task": "🤝",
    "wikipedia_search": "🌐",
    "web_search": "🔎",
    "web_fetch": "🌍",
    "ollama_web_search": "🦙",
    "write_file": "💾",
    "read_file": "📂",
    "list_skills": "📚",
    "view_skill": "👁️",
    "manage_skill": "🛠️",
    "cron_job": "⏰",
}


def _tool_emoji(name: str) -> str:
    return _TOOL_EMOJIS.get(name, "⚡")


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["◜", "◠", "◝", "◞", "◟", "◡"]
_THINKING_WORDS = itertools.cycle(["思考中", "推理中", "整理知识", "查询记忆"])

# Enable ANSI escape codes on Windows (requires Windows 10 1511+).
def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:  # noqa: BLE001
        pass


_enable_windows_ansi()


class Spinner:
    """Thread-based CLI spinner with ANSI in-place updates."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._text = ""

    def start(self, text: str = "") -> None:
        self._text = text
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update_text(self, text: str) -> None:
        self._text = text

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        # Clear the spinner line
        sys.stderr.write("\r" + " " * 60 + "\r")
        sys.stderr.flush()

    def _spin(self) -> None:
        word = next(_THINKING_WORDS)
        t0 = time.monotonic()
        for frame in itertools.cycle(_SPINNER_FRAMES):
            if self._stop_event.is_set():
                break
            elapsed = time.monotonic() - t0
            label = self._text or word
            line = f"  {frame} {label} ({elapsed:.1f}s)"
            sys.stderr.write(f"\r{line:<55}")
            sys.stderr.flush()
            time.sleep(0.12)


# ---------------------------------------------------------------------------
# Tool progress modes
# ---------------------------------------------------------------------------

_TOOL_PROGRESS_MODES = ("off", "new", "all", "verbose")
_NEXT_MODE: dict[str, str] = {
    "off": "new",
    "new": "all",
    "all": "verbose",
    "verbose": "off",
}


def _cprint(msg: str, **style_kwargs) -> None:
    """Print to stdout with optional click.style kwargs."""
    click.echo(click.style(msg, **style_kwargs) if style_kwargs else msg)


def build_callbacks(mode: str) -> AgentCallbacks:
    """Build AgentCallbacks for the given tool-progress display *mode*.

    Returns a fresh AgentCallbacks each call (state is per-turn).
    """
    _first_chunk: list[bool] = [True]

    if mode == "off":
        def _on_text_chunk(chunk: str) -> None:
            if _first_chunk[0]:
                sys.stdout.write(click.style("助手", fg="yellow") + " > ")
                sys.stdout.flush()
                _first_chunk[0] = False
            sys.stdout.write(chunk)
            sys.stdout.flush()

        return AgentCallbacks(
            on_text_chunk=_on_text_chunk,
            was_streamed=lambda: not _first_chunk[0],
        )

    spinner = Spinner()
    _last_tool: list[str] = [""]  # for "new" mode dedup

    def _on_thinking_start() -> None:
        spinner.start()

    def _on_thinking_end() -> None:
        spinner.stop()

    def _on_text_chunk(chunk: str) -> None:
        if _first_chunk[0]:
            sys.stdout.write(click.style("助手", fg="yellow") + " > ")
            sys.stdout.flush()
            _first_chunk[0] = False
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def _on_tool_start(tool_name: str, args: dict) -> None:
        if mode == "verbose":
            preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            _cprint(f"  ┊ {_tool_emoji(tool_name)} {tool_name}({preview})", dim=True)
        elif mode == "all":
            spinner.update_text(f"{_tool_emoji(tool_name)} {tool_name}")

    def _on_tool_end(
        tool_name: str, args: dict, result, duration: float
    ) -> None:
        success = True
        preview = ""
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                success = not (isinstance(parsed, dict) and "error" in parsed)
                if isinstance(parsed, dict):
                    preview = str(parsed.get("result", parsed.get("error", "")))
            except json.JSONDecodeError:
                preview = result
                success = True
        if mode == "new":
            if tool_name != _last_tool[0]:
                _cprint(f"  ┊ {_tool_emoji(tool_name)} {tool_name}", dim=True)
                _last_tool[0] = tool_name
        elif mode in ("all", "verbose"):
            status = "✓" if success else "✗"
            _cprint(
                f"  ┊ {_tool_emoji(tool_name)} {tool_name} {status} ({duration:.1f}s)",
                dim=True,
            )
            if mode == "verbose" and preview:
                summary_preview = preview[:80] + ("…" if len(preview) > 80 else "")
                _cprint(f"  │   {summary_preview}", dim=True)

    return AgentCallbacks(
        on_thinking_start=_on_thinking_start,
        on_thinking_end=_on_thinking_end,
        on_tool_start=_on_tool_start,
        on_tool_end=_on_tool_end,
        on_text_chunk=_on_text_chunk,
        was_streamed=lambda: not _first_chunk[0],
    )


@click.group()
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def cli(debug: bool) -> None:
    """EduAgent – AI-powered educational assistant."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


@cli.command()
@click.option("--user", default="default", show_default=True, help="User identifier.")
@click.option(
    "--skills",
    default="skills",
    show_default=True,
    help="Path to the skills directory containing *.md persona/strategy files.",
)
@click.option(
    "--max-iter",
    default=20,
    show_default=True,
    help="Max reasoning iterations per turn.",
)
@click.option(
    "--progress",
    default="all",
    show_default=True,
    type=click.Choice(_TOOL_PROGRESS_MODES),
    help="Tool progress display mode.",
)
@click.option(
    "--enable-cron",
    is_flag=True,
    default=False,
    help="Start the background CronDaemon for scheduled tasks.",
)
@click.option(
    "--session-id",
    default="",
    show_default=False,
    help="Resume an existing session (messages loaded from workspace/sessions.db).",
)
@click.option(
    "--disable-memory",
    is_flag=True,
    default=False,
    help="Disable long-term memory (no extraction, consolidation, or memory tools).",
)
@click.option(
    "--approve-all",
    is_flag=True,
    default=False,
    help="Skip interactive tool permission prompts (non-interactive / CI).",
)
@click.option(
    "--disable-tool",
    multiple=True,
    default=[],
    help="Disable specific tool names (repeatable).",
)
@click.option(
    "--allow-network",
    is_flag=True,
    default=False,
    help="Allow tools that declare NETWORK (merged with yaml permission_policy).",
)
@click.option(
    "--allow-write",
    is_flag=True,
    default=False,
    help="Allow tools that declare WRITE without separate approval (merged with yaml).",
)
@click.option(
    "--allow-execute",
    is_flag=True,
    default=False,
    help="Allow tools that declare EXECUTE.",
)
@click.option(
    "--allow-external",
    is_flag=True,
    default=False,
    help="Allow tools that declare EXTERNAL (e.g. MCP proxies).",
)
@click.option(
    "--course-id",
    default="",
    show_default=False,
    help="Optional course id for knowledge_query routing (session context).",
)
@click.option(
    "--gateway-mode",
    is_flag=True,
    default=False,
    help="Label Gateway E2E path (same stack as default; for CI and documentation).",
)
def chat(
    user: str,
    skills: str,
    max_iter: int,
    progress: str,
    enable_cron: bool,
    session_id: str,
    disable_memory: bool,
    approve_all: bool,
    disable_tool: tuple[str, ...],
    allow_network: bool,
    allow_write: bool,
    allow_execute: bool,
    allow_external: bool,
    course_id: str,
    gateway_mode: bool,
) -> None:
    """Start an interactive chat session (CLI → Gateway → SessionRunner → Agent)."""
    settings = load_settings()
    config = AgentConfig(
        user_id=user,
        skills_dir=skills,
        max_iterations=max_iter,
        session_id=session_id.strip(),
        memory_enabled=not disable_memory,
        approve_all_tools=approve_all,
        disabled_tools=[t.strip() for t in disable_tool if t.strip()],
        allow_network_tools=allow_network,
        allow_write_tools=allow_write,
        allow_execute_tools=allow_execute,
        allow_external_tools=allow_external,
        course_id=course_id.strip(),
    )
    paths = build_paths(settings, skills_dir=skills)
    store = SessionStore(paths.sessions_db)
    try:
        seed = EduAgent(config, settings=settings, session_store=store)
    except ValueError as exc:
        store.close()
        click.echo(click.style(f"[错误] {exc}", fg="red"), err=True)
        raise SystemExit(1) from exc

    context_manager = ContextManager(
        store,
        ContextConfig(model_max_tokens=seed._max_tokens),
        settings,
        model_name=seed._model,
        summarizer=seed._build_summarizer(),
    )
    del seed

    gw_raw = settings.runtime.gateway or {}
    gateway = Gateway(
        settings=settings,
        session_store=store,
        context_manager=context_manager,
        auth_checker=AuthorizationChecker(
            expected_api_key=str(gw_raw.get("api_key") or "").strip() or None
        ),
        queue_maxsize=int(gw_raw.get("queue_maxsize", 100)),
        outbound_queue_maxsize=int(gw_raw.get("outbound_queue_maxsize", 256)),
        runner_idle_timeout_sec=float(gw_raw.get("runner_idle_timeout_sec", 1800.0)),
        max_runners=int(gw_raw.get("max_runners", 256)),
        require_http_key=bool(gw_raw.get("require_http_key", False)),
    )
    adapter = CLIChannelAdapter(gateway)
    mode_state: list[str] = [progress]

    if enable_cron:
        from edu_agent.cron import CronDaemon
        _daemon = CronDaemon()
        _daemon.start()
        click.echo(click.style("[CronDaemon 已启动]", dim=True))

    click.echo(click.style(f"进度模式: {mode_state[0]}", dim=True))

    try:

        async def _run() -> None:
            await adapter.start()
            try:

                def _on_verbose() -> None:
                    mode_state[0] = _NEXT_MODE[mode_state[0]]
                    click.echo(click.style(f"[进度模式已切换为: {mode_state[0]}]", dim=True))

                def _on_mode_select(mode: str) -> None:
                    mode_state[0] = mode
                    click.echo(click.style(f"[进度模式已切换为: {mode_state[0]}]", dim=True))

                await adapter.run_chat_loop(
                    user_id=user,
                    initial_session_id=config.session_id,
                    get_progress_mode=lambda: mode_state[0],
                    on_mode_cycle=_on_verbose,
                    on_mode_select=_on_mode_select,
                    gateway_mode_label=gateway_mode,
                )
            finally:
                await adapter.stop()
                try:
                    await _shutdown_mcp_async()
                except Exception:
                    pass
                await gateway.stop()

        asyncio.run(_run())
    finally:
        store.close()


async def _shutdown_mcp_async() -> None:
    from edu_agent.mcp.integration import shutdown_mcp_servers

    await shutdown_mcp_servers()


@cli.group()
def channels() -> None:
    """External chat channels (WeChat personal via ilinkai — nanobot-style QR login)."""
    pass


@channels.command("login")
@click.argument("name", type=click.Choice(["weixin"], case_sensitive=False))
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Delete saved token and scan QR again.",
)
def channels_login(name: str, force: bool) -> None:
    """Save WeChat bot token (QR scan). Same flow as ``nanobot channels login weixin``."""
    _ = name
    settings = load_settings()
    paths = build_paths(settings)

    async def _run() -> bool:
        return await run_weixin_qr_login(settings=settings, paths=paths, force=force)

    try:
        ok = asyncio.run(_run())
    except KeyboardInterrupt:
        click.echo(click.style("Cancelled.", fg="yellow"))
        raise SystemExit(1) from None
    if ok:
        click.echo(
            click.style(
                "WeChat token saved under workspace .edu_agent/weixin/account.json. "
                "Set runtime.channels.weixin.enabled: true and start edu-gateway.",
                fg="green",
            )
        )
    else:
        click.echo(click.style("WeChat login failed.", fg="red"))
        raise SystemExit(1)


@cli.command("show-profile")
@click.option("--user", default="default", show_default=True, help="User identifier.")
def show_profile(user: str) -> None:
    """Display the persisted learner profile (A3 memory/profiles)."""
    import json as _json

    settings = load_settings()
    paths = build_paths(settings)
    from edu_agent.memory.storage import MemoryStore

    mstore = MemoryStore(paths.memory_dir)
    prof = mstore.load_profile(user)
    if prof is None:
        click.echo(click.style("（尚无画像文件；完成一次带记忆会话后可生成）", dim=True))
        return
    click.echo(_json.dumps(prof.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))


@cli.command("list-tools")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print machine-readable JSON (name, toolset, description).",
)
def list_tools(as_json: bool) -> None:
    """List tools exposed to the LLM for the current settings (toolsets + checks)."""
    from edu_agent.toolsets.registry import discover_builtin_tools, toolset_registry

    settings = load_settings()
    discover_builtin_tools()
    specs = toolset_registry.list_specs(settings)
    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "name": s.name,
                        "toolset": s.toolset,
                        "description": s.description,
                        "permissions": [p.value for p in s.permissions],
                        "approval_required": s.approval_required,
                    }
                    for s in specs
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    click.echo(click.style(f"已启用工具（{len(specs)}）", fg="green"))
    for s in specs:
        flags = []
        if s.approval_required:
            flags.append("需确认")
        extra = f"  [{', '.join(flags)}]" if flags else ""
        click.echo(f"  {_tool_emoji(s.name)} {s.name}  ({s.toolset}){extra}")


@cli.command("list-sessions")
@click.option("--user", default="default", show_default=True, help="User identifier.")
@click.option("--limit", default=20, show_default=True, type=int, help="Max sessions to list.")
def list_sessions(user: str, limit: int) -> None:
    """List recent sessions for a user (from workspace/sessions.db)."""
    settings = load_settings()
    paths = build_paths(settings)
    store = SessionStore(paths.sessions_db)
    try:
        sessions = store.search_sessions(user_id=user, limit=limit)
        if not sessions:
            click.echo(click.style("（无会话记录）", dim=True))
            return
        click.echo(click.style(f"最近会话（最多 {limit} 条）", fg="green"))
        for s in sessions:
            st = s.metadata.status.value
            title = s.metadata.title or ""
            click.echo(
                f"  {s.metadata.id}  [{st}]  updated={s.metadata.updated_at.isoformat()}  {title}"
            )
    finally:
        store.close()


@cli.command("cleanup-sessions")
@click.option(
    "--before",
    required=True,
    help="ISO date or datetime; delete sessions created strictly before this instant.",
)
@click.option(
    "--archived-only",
    is_flag=True,
    default=False,
    help="Only delete sessions already in ARCHIVED status.",
)
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def cleanup_sessions(before: str, archived_only: bool, yes: bool) -> None:
    """Delete old sessions from sessions.db (destructive)."""
    from datetime import datetime

    settings = load_settings()
    paths = build_paths(settings)
    store = SessionStore(paths.sessions_db)
    try:
        cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
    except ValueError as exc:
        store.close()
        raise click.BadParameter(f"Invalid --before datetime: {before}") from exc
    if not yes:
        click.confirm(
            f"Delete sessions created before {cutoff.isoformat()} "
            f"(archived_only={archived_only})?",
            abort=True,
        )
    n = store.delete_sessions_before(cutoff, archived_only=archived_only)
    store.close()
    click.echo(click.style(f"Deleted {n} session(s).", fg="green"))


if __name__ == "__main__":
    cli()
