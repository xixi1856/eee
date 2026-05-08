"""CLI entry point for the educational agent.

Usage:
    edu chat [--user USER] [--skills SKILLS_DIR]
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
import threading
import time

import click

from edu_agent.agent import EduAgent
from edu_agent.config_loader import load_settings
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
_THINKING_WORDS = itertools.cycle(["思考中\n", "推理中\n", "整理知识\n", "查询记忆\n"])

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
def chat(user: str, skills: str, max_iter: int, progress: str, enable_cron: bool) -> None:
    """Start an interactive chat session with the educational agent."""
    settings = load_settings()
    config = AgentConfig(user_id=user, skills_dir=skills, max_iterations=max_iter)
    agent = EduAgent(config, settings=settings)
    mode_state: list[str] = [progress]

    if enable_cron:
        from edu_agent.cron import CronDaemon
        _daemon = CronDaemon()
        _daemon.start()
        click.echo(click.style("[CronDaemon 已启动]", dim=True))

    click.echo(
        click.style("EduAgent 已启动。输入 /quit 或 /exit 退出，/reset 清空对话历史，/verbose 切换进度模式。", fg="green")
    )
    click.echo(click.style(f"会话 ID: {config.session_id}  进度模式: {mode_state[0]}", dim=True))

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

        # Build fresh callbacks for this turn (resets per-turn state like _first_chunk).
        cbs = build_callbacks(mode_state[0])
        agent.callbacks = cbs

        try:
            reply = agent.run_turn(stripped)
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent error: %s", exc)
            click.echo(click.style(f"[错误] {exc}", fg="red"), err=True)
            continue

        # If streaming emitted text, just add a newline.
        # If not (e.g. mocked agent or callbacks never called), print full reply.
        if cbs.on_text_chunk is not None and cbs.was_streamed and cbs.was_streamed():
            click.echo()  # end the streamed line
        else:
            click.echo(click.style("助手", fg="yellow") + " > " + reply)
        click.echo()


if __name__ == "__main__":
    cli()
