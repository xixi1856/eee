"""Click CLI entry point.

Usage examples:
    # 知识库
    ## 预处理和生成
    uv run rag ingest data/input/report.pdf
    uv run rag ingest data/input/
    uv run rag query "What are the main findings?"
    uv run rag query "Summarise the tables" --mode local
    uv run rag query "What are the main findings?" --refs
    ## 清除
    uv run rag clear-storage
    uv run rag reindex data/input/report.pdf
    uv run rag status
    ## 可视化知识图谱
    uv run rag visualise
    ## 出题
    uv run rag generate
    uv run rag generate -f data/input/应用层.pdf -n 15
    uv run rag generate -f data/input/应用层.pdf -f data/input/运输层.pdf -n 30 --alpha 0.7 --beta 0.3
    uv run rag generate -n 20 -w short_answer:1.0 -g application:0.6,synthesis:0.4
    uv run rag generate -n 10 -g innovation:1.0 -w single_choice:0.5,fill_blank:0.5
    ## minerU单独解析文件
    uv run parse data/input/input.pdf
    # mindmap
    uv run mindmap data/input/report.pdf --structured --output output/mindmaps/report_mindmap.json --refine
    uv run mindmap data/input/report.pdf --llm
    uv run mindmap render <md文件路径>
"""

import atexit
from pathlib import Path
from typing import IO

import click
from loguru import logger

_LOG_FILE = Path("logs.txt")
_log_fh: IO[str] | None = None  # file handle kept open for the process lifetime


def _setup_logging() -> None:
    """Attach loguru file sink and patch click.echo to tee into logs.txt."""
    global _log_fh
    if _log_fh is not None:
        return  # guard against double-invoke

    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _log_fh = _LOG_FILE.open("a", encoding="utf-8")

    # loguru: append structured lines (logger.info / .error / etc.)
    logger.add(
        str(_LOG_FILE),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        level="DEBUG",
        encoding="utf-8",
        colorize=False,
    )

    # Patch click.echo so every echo call also writes to logs.txt.
    # Click 8.x caches the underlying stream wrapper, so replacing
    # sys.stdout after import has no effect – patching echo directly
    # is the only reliable way to intercept all CLI output.
    _orig_echo = click.echo

    def _tee_echo(message=None, file=None, nl=True, err=False, color=None):
        _orig_echo(message, file=file, nl=nl, err=err, color=color)
        if file is None and _log_fh is not None:
            text = (str(message) if message is not None else "") + ("\n" if nl else "")
            _log_fh.write(text)
            _log_fh.flush()

    click.echo = _tee_echo  # type: ignore[assignment]

    atexit.register(lambda: (_log_fh.flush(), _log_fh.close()) if _log_fh is not None else None)


@click.group()
def cli() -> None:
    """Multimodal RAG CLI – ingest documents and query knowledge base."""
    _setup_logging()


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def ingest(path: Path) -> None:
    """Ingest a single FILE or all documents inside a FOLDER into the knowledge base.

    \b
    Supported formats: PDF, DOCX, PPTX, XLSX, TXT, MD, JPG, PNG
    (DOCX/PPTX require LibreOffice to be installed.)
    """
    from .engine import ingest_file, ingest_folder

    try:
        if path.is_dir():
            ingest_folder(path)
        else:
            ingest_file(path)

        # Report image filter stats if filtering was enabled
        from .config import settings as _cfg
        if _cfg.enable_image_filter:
            from .llm import get_image_filter_stats
            stats = get_image_filter_stats()
            click.echo(
                f"[image-filter] 总计 {stats['total_images']} 张图片 | "
                f"保留 {stats['useful_images']} | "
                f"过滤 {stats['filtered_images']} | "
                f"错误 {stats['filter_errors']}"
            )
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def parse(path: Path) -> None:
    """Parse a single FILE or all documents inside a FOLDER with MinerU (no indexing).

    \b
    Output is written to output/parsed/. No RAG index is built.
    Use this when you only need the parsed Markdown / JSON output,
    or before running 'rag reindex' manually.

    Supported formats: PDF, DOCX, PPTX, XLSX, TXT, MD, JPG, PNG
    """
    from .engine import parse_file, parse_folder

    try:
        if path.is_dir():
            parse_folder(path)
        else:
            parse_file(path)
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command()
@click.argument("question")
@click.option(
    "--mode",
    type=click.Choice(["hybrid", "local", "global", "naive"], case_sensitive=False),
    default="hybrid",
    show_default=True,
    help="Retrieval mode.",
)
@click.option(
    "--refs",
    is_flag=True,
    default=False,
    help="Show source chunks, entities and file references used to answer.",
)
def query(question: str, mode: str, refs: bool) -> None:
    """Query the knowledge base with a natural-language QUESTION."""
    from .engine import query as do_query

    try:
        result = do_query(question, mode=mode, with_refs=refs)
        if result is None:
            click.echo("[No answer returned – knowledge base may be empty or query failed.]")
            return

        if not refs:
            click.echo(result)
            return

        # ---- structured output with citations ----
        assert isinstance(result, dict)
        answer = result.get("answer", "")
        chunks = result.get("chunks", [])
        entities = result.get("entities", [])
        refs_list = result.get("references", [])

        click.echo(answer)

        if refs_list:
            click.echo("\n" + "─" * 60)
            click.echo("引用来源 (References)")
            click.echo("─" * 60)
            for ref in refs_list:
                click.echo(f"  [{ref.get('reference_id', '?')}] {ref.get('file_path', '')}")

        if chunks:
            click.echo("\n" + "─" * 60)
            click.echo("引用片段 (Chunks)")
            click.echo("─" * 60)
            for i, chunk in enumerate(chunks, 1):
                ref_id = chunk.get("reference_id", "")
                fp = chunk.get("file_path", "")
                content = chunk.get("content", "").strip()
                click.echo(f"\n[{ref_id or i}] {fp}")
                # Indent the content block
                for line in content.splitlines():
                    click.echo(f"    {line}")

        if entities:
            click.echo("\n" + "─" * 60)
            click.echo(f"相关实体 (Entities, top {min(len(entities), 10)})")
            click.echo("─" * 60)
            for ent in entities[:10]:
                click.echo(
                    f"  {ent.get('entity_name', '')} [{ent.get('entity_type', '')}]"
                    f" – {ent.get('description', '')[:80]}"
                )
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command()
def status() -> None:
    """Show knowledge base storage statistics."""
    from .config import settings

    working_dir = settings.working_dir
    output_dir = settings.output_dir

    click.echo(f"Working dir : {working_dir.resolve()}")
    click.echo(f"Output dir  : {output_dir.resolve()}")

    if not working_dir.exists():
        click.echo("Status      : not initialised (run 'rag ingest' first)")
        return

    total_bytes = sum(f.stat().st_size for f in working_dir.rglob("*") if f.is_file())
    total_mb = total_bytes / (1024 * 1024)

    parsed_files = list(output_dir.rglob("*.md")) if output_dir.exists() else []
    cached_jsons = (
        [p for p in output_dir.rglob("*_content_list.json") if "_content_list_v2" not in p.name]
        if output_dir.exists()
        else []
    )

    # Show stored embedding metadata if available
    metadata_path = working_dir / ".metadata.json"
    if metadata_path.exists():
        import json
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        click.echo(f"Embed model : {meta.get('embedding_model')} (dim={meta.get('embedding_dim')})")

    click.echo(f"Storage size: {total_mb:.1f} MB")
    click.echo(f"Parsed docs : {len(parsed_files)} markdown files")
    click.echo(f"Cached parse: {len(cached_jsons)} content_list.json available for reindex")


@cli.command()
@click.argument("path", type=click.Path(path_type=Path), required=False, default=None)
@click.option(
    "--output-dir",
    default=None,
    help="Directory to scan for cached parse results (default: output/parsed).",
)
def reindex(path: Path | None, output_dir: str | None) -> None:
    """Rebuild the index from existing MinerU parse cache (no re-parsing).

    \b
    PATH can be:
      - Omitted          : reindex all cached documents under output/parsed/
      - An original file : e.g. data/input/report.pdf  (reindex that file only)
      - A folder         : reindex all cached docs whose stem matches files in the folder

    Use this after:
      - Running 'rag clear-storage' due to an embedding dimension change
      - Migrating to a new graph / storage configuration
    The output/parsed/ directory must contain *_content_list.json files.
    """
    from .engine import reindex_from_cache

    try:
        reindex_from_cache(output_dir=output_dir, file_path=path)

        # Report image filter stats if filtering was enabled
        from .config import settings as _cfg
        if _cfg.enable_image_filter:
            from .llm import get_image_filter_stats
            stats = get_image_filter_stats()
            click.echo(
                f"[image-filter] 总计 {stats['total_images']} 张图片 | "
                f"保留 {stats['useful_images']} | "
                f"过滤 {stats['filtered_images']} | "
                f"错误 {stats['filter_errors']}"
            )
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command("clear-storage")
@click.confirmation_option(prompt="This will delete rag_storage/ (vectors + graph). Continue?")
def clear_storage_cmd() -> None:
    """Delete the vector/graph storage so it can be rebuilt via 'rag reindex'.

    \b
    The parsed output in output/ is NOT deleted.
    After clearing, run:
      uv run rag reindex
    """
    from .engine import clear_storage

    try:
        clear_storage()
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc

@cli.command("generate")
@click.option(
    "--file", "-f",
    "files",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Source document(s) to restrict question scope. Repeatable. Omit for all indexed docs.",
)
@click.option("--count", "-n", default=20, show_default=True, help="Total number of questions to generate.")
@click.option(
    "--output", "-o",
    default="output/questions",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Output directory for JSON and Markdown results.",
)
@click.option("--alpha", default=0.6, show_default=True, help="Weight for chunk-frequency in entity importance score.")
@click.option("--beta", default=0.4, show_default=True, help="Weight for graph-degree in entity importance score.")
@click.option(
    "--type-weights", "-w",
    "type_weights_str",
    default=None,
    help=(
        "Question type proportions as 'type:weight,...'. "
        "Valid types: single_choice, multi_choice, fill_blank, short_answer. "
        "Weights need not sum to exactly 1 (they are normalised). "
        "Example: --type-weights single_choice:0.4,multi_choice:0.2,fill_blank:0.2,short_answer:0.2"
    ),
)
@click.option(
    "--objective-weights", "-g",
    "objective_weights_str",
    default=None,
    help=(
        "Cognitive objective proportions as 'objective:weight,...'. "
        "Valid objectives: knowledge, comprehension, application, synthesis, innovation. "
        "Weights need not sum to exactly 1 (they are normalised). "
        "Interacts with --type-weights via a compatibility matrix; "
        "zero-compatibility pairs (e.g. innovation + fill_blank) are excluded automatically. "
        "Example: -g application:0.5,synthesis:0.3,innovation:0.2"
    ),
)
def generate_cmd(
    files: tuple[Path, ...],
    count: int,
    output: Path,
    alpha: float,
    beta: float,
    type_weights_str: str | None,
    objective_weights_str: str | None,
) -> None:
    """Generate exam questions from indexed documents using entity importance ranking.

    \b
    Entity importance = alpha × chunk_frequency + beta × graph_degree
    Default format  distribution: single_choice 40%, multi_choice 10%, fill_blank 30%, short_answer 20%
    Default objective distribution: knowledge 30%, comprehension 20%, application 30%, synthesis 10%, innovation 10%

    \b
    Both -w and -g interact via a compatibility matrix. Invalid combinations
    (e.g. innovation + fill_blank) are excluded automatically.

    \b
    Examples:
      uv run rag generate -n 20
      uv run rag generate -f data/input/\u5e94\u7528\u5c42.pdf -n 15
      uv run rag generate -n 20 -w single_choice:0.5,fill_blank:0.3,short_answer:0.2
      uv run rag generate -n 20 -g application:0.5,synthesis:0.3,innovation:0.2
      uv run rag generate -n 20 -w short_answer:1.0 -g application:0.6,synthesis:0.4
    """
    from .question_gen import DEFAULT_TYPE_WEIGHTS, DEFAULT_OBJECTIVE_WEIGHTS, OBJECTIVE_TYPES, generate, save_output

    file_paths = list(files) if files else None

    # Parse --type-weights if provided
    type_weights: dict[str, float] | None = None
    if type_weights_str:
        valid_types = set(DEFAULT_TYPE_WEIGHTS.keys())
        try:
            pairs = [item.split(":") for item in type_weights_str.split(",")]
            type_weights = {k.strip(): float(v.strip()) for k, v in pairs}
        except (ValueError, IndexError) as exc:
            raise click.BadParameter(
                f"Invalid format: {exc}. Expected 'type:weight,...'", param_hint="--type-weights"
            ) from exc
        unknown = set(type_weights) - valid_types
        if unknown:
            raise click.BadParameter(
                f"Unknown type(s): {unknown}. Valid: {valid_types}", param_hint="--type-weights"
            )
        # Normalise so weights sum to 1.0
        total_w = sum(type_weights.values())
        if total_w <= 0:
            raise click.BadParameter("Weights must be positive.", param_hint="--type-weights")
        type_weights = {k: v / total_w for k, v in type_weights.items()}

    # Parse --objective-weights if provided
    objective_weights: dict[str, float] | None = None
    if objective_weights_str:
        valid_objectives = set(OBJECTIVE_TYPES.keys())
        try:
            obj_pairs = [item.split(":") for item in objective_weights_str.split(",")]
            objective_weights = {k.strip(): float(v.strip()) for k, v in obj_pairs}
        except (ValueError, IndexError) as exc:
            raise click.BadParameter(
                f"Invalid format: {exc}. Expected 'objective:weight,...'", param_hint="--objective-weights"
            ) from exc
        unknown_obj = set(objective_weights) - valid_objectives
        if unknown_obj:
            raise click.BadParameter(
                f"Unknown objective(s): {unknown_obj}. Valid: {valid_objectives}",
                param_hint="--objective-weights",
            )
        total_ow = sum(objective_weights.values())
        if total_ow <= 0:
            raise click.BadParameter("Weights must be positive.", param_hint="--objective-weights")
        objective_weights = {k: v / total_ow for k, v in objective_weights.items()}

    try:
        result = generate(file_paths=file_paths, count=count, alpha=alpha, beta=beta, type_weights=type_weights, objective_weights=objective_weights)
        json_path, md_path = save_output(result, output)
        click.echo(f"Generated {result['total']} questions")
        click.echo(f"JSON     : {json_path.resolve()}")
        click.echo(f"Markdown : {md_path.resolve()}")
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


@cli.command("visualise")
@click.option("--output", default=None, type=click.Path(path_type=Path), help="Output HTML path (default: rag_storage/graph_viz.html).")
@click.option("--max-nodes", default=500, show_default=True, help="Max nodes to render (top by degree).")
@click.option("--no-browser", is_flag=True, default=False, help="Generate HTML but do not open browser.")
def visualise(output: Path | None, max_nodes: int, no_browser: bool) -> None:
    """Generate a D3.js interactive knowledge graph and open it in the browser.

    \b
    Features:
      - Force-directed layout, colour-coded by entity type
      - Drag nodes, zoom/pan, search, adjust physics sliders
      - Double-click a node to pin/unpin it
      - Hover nodes/edges for details
    """
    from .graph_viz import open_graph

    try:
        html_path = open_graph(output_html=output, max_nodes=max_nodes, no_browser=no_browser)
        click.echo(f"Graph saved to: {html_path.resolve()}")
        if not no_browser:
            click.echo("Opening in browser…")
    except Exception as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc