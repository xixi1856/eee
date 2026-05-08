"""CLI entry point for the standalone `mindmap` command.

Usage:
    mindmap 运输层
    mindmap 运输层 --mode llm
    mindmap output/parsed/运输层/.../运输层.md
    mindmap data/ --mode structure
    mindmap 运输层 --no-browser
"""

from __future__ import annotations

from pathlib import Path

import sys

import click
from loguru import logger


@click.group()
@click.option("--debug", is_flag=True, default=False, help="Show DEBUG-level logs including raw LLM responses.")
@click.pass_context
def cli(ctx: click.Context, debug: bool) -> None:
    """Mindmap generator – build interactive mind maps from parsed documents."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    if debug:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG", colorize=True,
                   format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")


@cli.command()
@click.argument("source")
@click.option(
    "--mode",
    type=click.Choice(["structure", "llm"], case_sensitive=False),
    default="structure",
    show_default=True,
    help="structure: parse MD headings (instant, no API calls). llm: LLM-based concept extraction.",
)
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory for HTML files (default: mindmap_storage/).",
)
@click.option("--no-browser", is_flag=True, default=False, help="Generate HTML but do not open browser.")
@click.option(
    "--max-chars",
    default=2500,
    show_default=True,
    help="[llm mode] Max characters per chunk sent to LLM.",
)
@click.option(
    "--refine",
    is_flag=True,
    default=False,
    help="[structure mode] Phase 1: send the full document to Qwen-Long for semantic aggregation before parsing headings.",
)
@click.pass_context
def generate(ctx: click.Context, source: str, mode: str, output_dir: Path | None, no_browser: bool, max_chars: int, refine: bool) -> None:
    """Generate a mind map from SOURCE (file stem, .md path, or folder).

    \b
    Examples:
      mindmap generate 运输层
      mindmap generate 运输层 --mode llm
      mindmap generate 运输层 --refine
      mindmap generate output/parsed/运输层/.../运输层.md
      mindmap generate data/ --mode structure
    """
    from .mindmap import build_structure_mindmap, build_llm_mindmap, MINDMAP_DIR
    import webbrowser

    out = output_dir or MINDMAP_DIR
    out.mkdir(parents=True, exist_ok=True)

    try:
        if mode == "structure":
            paths = build_structure_mindmap(source, refine=refine)
        else:
            paths = build_llm_mindmap(source, max_chars=max_chars)

        for p in paths:
            click.echo(f"Mindmap saved: {p.resolve()}")
            if not no_browser:
                webbrowser.open(p.resolve().as_uri())
    except FileNotFoundError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command()
@click.argument("md_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(path_type=Path),
    help="Output HTML file path (default: mindmap_storage/mindmap_<stem>.html).",
)
@click.option("--no-browser", is_flag=True, default=False, help="Generate HTML but do not open browser.")
@click.pass_context
def render(ctx: click.Context, md_file: Path, output: Path | None, no_browser: bool) -> None:
    """Render a Markdown file directly to an interactive HTML mind map.

    \b
    Examples:
      mindmap render output/parsed/运输层/.../运输层.md
      mindmap render notes.md --output my_map.html
      mindmap render notes.md --no-browser
    """
    from .mindmap import _parse_md_tree, _write_html, MINDMAP_DIR
    import webbrowser

    out = output or (MINDMAP_DIR / f"mindmap_{md_file.stem}.html")
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        tree = _parse_md_tree(md_file)
        _write_html(tree, md_file.stem, out)
        click.echo(f"Mindmap saved: {out.resolve()}")
        if not no_browser:
            webbrowser.open(out.resolve().as_uri())
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
