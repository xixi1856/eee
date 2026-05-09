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
from edu_agent.config_loader import load_settings
from edu_agent.paths import build_paths
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
) -> None:
    """Start an interactive chat session with the educational agent."""
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
        agent = EduAgent(config, settings=settings, session_store=store)
    except ValueError as exc:
        store.close()
        click.echo(click.style(f"[错误] {exc}", fg="red"), err=True)
        raise SystemExit(1) from exc
    mode_state: list[str] = [progress]

    if enable_cron:
        from edu_agent.cron import CronDaemon
        _daemon = CronDaemon()
        _daemon.start()
        click.echo(click.style("[CronDaemon 已启动]", dim=True))

    click.echo(
        click.style(
            "EduAgent 已启动。/quit 或 /exit 退出；/reset 清空对话；/verbose 切换进度；"
            "/compress-context（或 /ctx-compress）手动压缩会话上下文。",
            fg="green",
        )
    )
    click.echo(click.style(f"会话 ID: {config.session_id}  进度模式: {mode_state[0]}", dim=True))

    try:
        asyncio.run(_chat_async_loop(agent, mode_state))
    finally:
        try:
            asyncio.run(_shutdown_mcp_async())
        except RuntimeError:
            pass
        try:
            agent.finalize_memory_session()
        except Exception as exc:  # noqa: BLE001
            logger.exception("finalize_memory_session: %s", exc)
        store.close()


async def _shutdown_mcp_async() -> None:
    from edu_agent.mcp.integration import shutdown_mcp_servers

    await shutdown_mcp_servers()


async def _chat_async_loop(agent: EduAgent, mode_state: list[str]) -> None:
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
            agent.reset()
            click.echo(click.style("[对话历史已清空]", dim=True))
            continue

        if stripped == "/verbose":
            mode_state[0] = _NEXT_MODE[mode_state[0]]
            click.echo(click.style(f"[进度模式已切换为: {mode_state[0]}]", dim=True))
            continue

        if stripped in ("/compress-context", "/ctx-compress"):
            if not agent.has_context_manager:
                click.echo(
                    click.style("[未启用会话存储，无法压缩上下文]", dim=True),
                    err=True,
                )
                continue
            if not agent.context_compression_active:
                click.echo(click.style("[上下文压缩已在配置中关闭]", dim=True))
                continue
            try:
                agent.trigger_context_compress()
                click.echo(click.style("[上下文压缩已完成]", dim=True))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Context compress failed: %s", exc)
                click.echo(click.style(f"[上下文压缩失败] {exc}", fg="red"), err=True)
            continue

        cbs = build_callbacks(mode_state[0])
        agent.callbacks = cbs

        try:
            reply = await agent.run_turn(stripped)
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent error: %s", exc)
            click.echo(click.style(f"[错误] {exc}", fg="red"), err=True)
            continue

        if cbs.on_text_chunk is not None and cbs.was_streamed and cbs.was_streamed():
            click.echo()
        else:
            click.echo(click.style("助手", fg="yellow") + " > " + reply)
        click.echo()


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
