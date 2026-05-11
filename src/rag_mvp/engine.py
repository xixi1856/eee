"""RAGAnything engine - initialisation and high-level ingest/query helpers."""

import asyncio
import json
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

from lightrag import LightRAG, QueryParam
from loguru import logger
from lightrag.utils import compute_mdhash_id
from raganything import RAGAnything, RAGAnythingConfig

from .config import settings
from .course_workspace import course_id_to_workspace
from .mineru_cloud import MineruCloudError, parse_file_via_cloud
from .embedding_factory import build_embedding_func
from .llm import (
    _filtered_vision_model_func,
    build_data_uri_from_image_path,
    ensure_embedding_backend_reachable,
    llm_model_func,
    vision_model_func,
)
from .postgres_env import ensure_postgres_env_from_database_url

# ---------------------------------------------------------------------------
# Metadata helpers – persist embedding config so mismatches are caught early
# ---------------------------------------------------------------------------
_METADATA_FILE = "rag_storage/.metadata.json"


def _write_metadata() -> None:
    path = Path(_METADATA_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "embedding_mode": settings.embedding_mode,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_metadata() -> None:
    """Raise RuntimeError if the stored embedding config differs from current settings."""
    path = Path(_METADATA_FILE)
    if not path.exists():
        return  # first run – no stored metadata yet
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored.get("embedding_dim") != settings.embedding_dim:
        raise RuntimeError(
            f"Embedding dimension mismatch: stored index uses dim={stored['embedding_dim']} "
            f"(model: {stored.get('embedding_model')}), but current config uses "
            f"dim={settings.embedding_dim} (model: {settings.embedding_model}).\n"
            "Run 'rag clear-storage' to delete the existing index, then re-ingest."
        )
    if stored.get("embedding_mode") != settings.embedding_mode:
        logger.warning(
            f"Embedding mode changed: stored={stored.get('embedding_mode')!r} "
            f"current={settings.embedding_mode!r}. "
            "Vectors may be incompatible; run 'rag clear-storage' and re-ingest."
        )
    if stored.get("embedding_model") != settings.embedding_model:
        logger.warning(
            f"Embedding model changed: stored='{stored.get('embedding_model')}' "
            f"current='{settings.embedding_model}'. "
            "Vectors may be incompatible; run 'rag clear-storage' and re-ingest."
        )

# Supported document extensions
_SUPPORTED_SUFFIXES = frozenset({
    ".pdf",
    ".doc", ".docx",
    ".ppt", ".pptx",
    ".xls", ".xlsx",
    ".txt", ".md",
    ".jpg", ".jpeg", ".png",
})

_IMAGE_QUERY_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
_QUERY_IMAGE_TOP_K = 60
_QUERY_IMAGE_CHUNK_TOP_K = 40
_QUERY_IMAGE_MAX_PARSED_CHARS = 100_000

async def _ensure_lightrag_storages(rag: RAGAnything) -> None:
    """Initialise LightRAG storages on the **current** asyncio loop (must not cross ``asyncio.run``)."""
    lr = rag.lightrag
    if lr is None:
        return
    await lr.initialize_storages()


def _invalidate_personal_rag_cache() -> None:
    """Drop cached personal RAG so the next ``asyncio.run`` gets a fresh LightRAG + embedding workers."""
    global _rag_instance
    _rag_instance = None


def _invalidate_course_rag_cache_for(course_id: str) -> None:
    """Drop cached course RAG for this course (sync wrappers use one-shot event loops)."""
    ws = course_id_to_workspace(course_id)
    _course_cache.pop(ws, None)


_course_cache: dict[str, RAGAnything] = {}
_course_init_lock = asyncio.Lock()


_VISION_QUERY_SYSTEM = (
    "You are a careful assistant. Answer using ONLY: (1) the retrieved knowledge base excerpts, "
    "(2) the user-provided image(s) when relevant, (3) the user's question. "
    "If excerpts are empty, say so and answer from the image(s) and question. "
    "Prefer the same language as the user's question."
)
_TEXT_IMAGE_FALLBACK_SYSTEM = (
    "You are a careful assistant. The vision API was unavailable; image content below was "
    "extracted via document parsing (OCR/layout). Answer using the retrieved passages, "
    "that parsed text, and the user's question. Prefer the same language as the question."
)


def _build_rag() -> RAGAnything:
    """Instantiate and return a configured RAGAnything instance.

    LightRAG storages are **not** initialised here; callers must
    ``await _ensure_lightrag_storages(rag)`` inside the same ``asyncio.run``
    as ingest/query so embedding workers stay on one event loop.
    """
    _check_metadata()  # abort early if embedding dim has changed
    settings.working_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    emb = build_embedding_func()
    lightrag = LightRAG(
        working_dir=str(settings.working_dir),
        llm_model_func=llm_model_func,
        embedding_func=emb,
        llm_model_max_async=settings.llm_max_async,
        embedding_func_max_async=settings.embedding_max_async,
        max_parallel_insert=settings.max_parallel_insert,
    )
    _write_metadata()

    config = RAGAnythingConfig(
        working_dir=str(settings.working_dir),
        parser_output_dir=str(settings.output_dir),
        parser=settings.parser,
        parse_method=settings.parse_method,
        enable_image_processing=True,
        enable_table_processing=True,
        enable_equation_processing=True,
    )

    return RAGAnything(
        lightrag=lightrag,
        config=config,
        llm_model_func=llm_model_func,
        vision_model_func=_filtered_vision_model_func if settings.enable_image_filter else vision_model_func,
        embedding_func=emb,
    )


# Lazy singleton - created on first use so imports do not fail before .env is loaded
_rag_instance: RAGAnything | None = None


def get_rag() -> RAGAnything:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = _build_rag()
    return _rag_instance


# MinerU extra kwargs forwarded to process_document_complete()
# process_folder_complete does NOT accept **kwargs, so folder ingestion
# iterates files and calls process_document_complete per file.
def _mineru_kwargs() -> dict:
    return {
        "backend": settings.mineru_backend,
        "device": settings.mineru_device,
        "source": settings.mineru_source,
        "lang": settings.mineru_lang,
        "formula": True,
        "table": True,
    }


def mineru_kwargs() -> dict:
    """Single source for MinerU kwargs (parse_document / ingest paths)."""
    return _mineru_kwargs()


async def _aingest_file_from_cloud_output(rag: RAGAnything, file_path: Path, out_dir: Path) -> None:
    """Index already-parsed cloud output (content_list JSON) into LightRAG."""
    json_files = [
        p for p in out_dir.rglob("*_content_list.json")
        if "_content_list_v2" not in p.name
    ]
    if not json_files:
        raise FileNotFoundError(f"MinerU Cloud 解压后未找到 *_content_list.json: {out_dir}")
    for json_path in json_files:
        stem = json_path.stem.replace("_content_list", "")
        raw: list = json.loads(json_path.read_text(encoding="utf-8"))
        content_list = _fix_image_paths(raw, json_path.parent)
        await rag.insert_content_list(content_list, file_path=stem)
        logger.debug("Cloud indexed: {} ({} blocks)", stem, len(content_list))


async def _aingest_file(rag: RAGAnything, file_path: Path) -> None:
    """Async: parse and index a single document file.

    Tries MinerU Cloud API first when configured; falls back to local MinerU
    on failure (if ``mineru_cloud_fallback_local`` is True).
    """
    out_dir = settings.output_dir / file_path.stem

    if settings.mineru_cloud_enabled and settings.mineru_cloud_api_key:
        try:
            await parse_file_via_cloud(file_path, out_dir)
            await _aingest_file_from_cloud_output(rag, file_path, out_dir)
            logger.success(f"Ingested (cloud): {file_path.name}")
            return
        except MineruCloudError as exc:
            if settings.mineru_cloud_fallback_local:
                logger.warning(
                    "MinerU Cloud 解析失败，降级到本地: {} — {}", file_path.name, exc
                )
            else:
                raise

    # Local MinerU (original path / fallback)
    await rag.process_document_complete(
        file_path=str(file_path),
        output_dir=str(out_dir),
        **_mineru_kwargs(),
    )
    logger.success(f"Ingested (local): {file_path.name}")


def ingest_file(file_path: str | Path) -> None:
    """Parse and index a single document file."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if file_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")

    logger.info(f"Ingesting file: {file_path}")
    rag = get_rag()

    async def _main() -> None:
        await _ensure_lightrag_storages(rag)
        await _aingest_file(rag, file_path)

    try:
        asyncio.run(_main())
    finally:
        _invalidate_personal_rag_cache()


def ingest_folder(folder_path: str | Path) -> None:
    """Parse and index all supported documents in a folder."""
    folder_path = Path(folder_path)
    if not folder_path.is_dir():
        raise NotADirectoryError(folder_path)

    files = [f for f in folder_path.rglob("*") if f.suffix.lower() in _SUPPORTED_SUFFIXES]
    if not files:
        logger.warning(f"No supported files found in {folder_path}")
        return

    logger.info(f"Found {len(files)} file(s) in {folder_path}")
    rag = get_rag()

    async def _process_all():
        await _ensure_lightrag_storages(rag)
        for f in files:
            await _aingest_file(rag, f)

    try:
        asyncio.run(_process_all())
    finally:
        _invalidate_personal_rag_cache()
    logger.success(f"Folder ingested: {folder_path} ({len(files)} files)")


def _validate_image_paths_for_query(paths: Sequence[Path]) -> None:
    for p in paths:
        suf = p.suffix.lower()
        if suf not in _IMAGE_QUERY_SUFFIXES:
            raise ValueError(
                f"Unsupported image type for --image: {p.name!r} ({suf!r}). "
                f"Allowed: {', '.join(sorted(_IMAGE_QUERY_SUFFIXES))}"
            )


def _chunks_to_context_text(chunks: list[Any]) -> str:
    parts: list[str] = []
    for i, ch in enumerate(chunks):
        if not isinstance(ch, dict):
            continue
        fp = ch.get("file_path", "") or ""
        content = str(ch.get("content") or "").strip()
        ref = ch.get("reference_id", i + 1)
        parts.append(f"[{ref}] ({fp})\n{content}")
    return "\n\n---\n\n".join(parts) if parts else "(No retrieved passages.)"


def _collect_parsed_markdown_under(dirs: Sequence[Path]) -> str:
    texts: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for md in sorted(d.rglob("*.md")):
            try:
                texts.append(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    blob = "\n\n".join(texts)
    if len(blob) > _QUERY_IMAGE_MAX_PARSED_CHARS:
        return blob[:_QUERY_IMAGE_MAX_PARSED_CHARS] + "\n\n[... truncated ...]"
    return blob


async def _aquery_data_for_image_query(
    question: str,
    mode: str,
    *,
    include_references: bool,
) -> dict[str, Any]:
    from lightrag import QueryParam

    rag = get_rag()
    await _ensure_lightrag_storages(rag)
    lightrag = rag.lightrag
    assert lightrag is not None, "LightRAG instance not initialised"
    m = cast(
        "Literal['local', 'global', 'hybrid', 'naive', 'mix', 'bypass']",
        mode,
    )
    param = QueryParam(
        mode=m,
        top_k=_QUERY_IMAGE_TOP_K,
        chunk_top_k=_QUERY_IMAGE_CHUNK_TOP_K,
        include_references=include_references,
    )
    return await lightrag.aquery_data(question.strip(), param)


async def _answer_with_images_async(
    question: str,
    mode: str,
    image_paths: tuple[Path, ...],
    *,
    with_refs: bool,
) -> str | dict:
    raw = await _aquery_data_for_image_query(
        question, mode, include_references=with_refs
    )
    data = raw.get("data") or {}
    chunks: list[Any] = list(data.get("chunks") or [])
    entities: list[Any] = list(data.get("entities") or [])
    relationships: list[Any] = list(data.get("relationships") or [])
    references: list[Any] = list(data.get("references") or [])

    context_text = _chunks_to_context_text(chunks)
    user_text = (
        "## Retrieved passages\n"
        + context_text
        + "\n\n## User question\n"
        + question.strip()
    )

    content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img_path in image_paths:
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": build_data_uri_from_image_path(img_path)},
            }
        )
    messages = [
        {"role": "system", "content": _VISION_QUERY_SYSTEM},
        {"role": "user", "content": content_parts},
    ]

    answer = ""
    try:
        answer = await vision_model_func("", messages=messages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision multimodal query failed: {}", exc)

    if not (answer or "").strip():
        logger.warning("Vision unavailable or empty; falling back to MinerU parse + text LLM")
        parsed_sections: list[str] = []
        for img_path in image_paths:
            parse_file(img_path)
            stem_dir = settings.output_dir / img_path.stem
            md_blob = _collect_parsed_markdown_under([stem_dir])
            label = img_path.name
            parsed_sections.append(f"### Parsed from {label}\n{md_blob or '(no markdown produced)'}")

        parsed_blob = "\n\n".join(parsed_sections)
        fallback_body = (
            "## Retrieved passages\n"
            + context_text
            + "\n\n## Parsed image content (MinerU)\n"
            + parsed_blob
            + "\n\n## User question\n"
            + question.strip()
        )
        answer = await llm_model_func(fallback_body, system_prompt=_TEXT_IMAGE_FALLBACK_SYSTEM)

    if not with_refs:
        return answer

    return {
        "answer": answer,
        "chunks": chunks,
        "entities": entities,
        "relationships": relationships,
        "references": references,
    }


def query(
    question: str,
    mode: str = "hybrid",
    with_refs: bool = False,
    image_paths: Sequence[Path] | None = None,
) -> str | dict:
    """Run a RAG query and return the answer.

    Args:
        question: Natural-language question.
        mode: Retrieval mode (hybrid / local / global / naive).
        with_refs: When True, returns a dict with keys ``answer``,
            ``chunks``, ``entities``, ``relationships`` and ``references``
            instead of a plain string.
        image_paths: Optional image files (``.jpg`` / ``.jpeg`` / ``.png``).
            When set, retrieval uses ``aquery_data`` then a vision LLM step
            (with MinerU parse + text LLM fallback if vision fails).
    """
    from typing import Literal, cast
    from lightrag import QueryParam

    _mode = cast(
        "Literal['local', 'global', 'hybrid', 'naive', 'mix', 'bypass']",
        mode,
    )
    rag = get_rag()

    if image_paths:
        paths = tuple(Path(p).resolve() for p in image_paths)
        if not paths:
            raise ValueError("image_paths must not be empty when provided")
        _validate_image_paths_for_query(paths)
        try:
            return asyncio.run(
                _answer_with_images_async(question, mode, paths, with_refs=with_refs)
            )
        finally:
            _invalidate_personal_rag_cache()

    if not with_refs:

        async def _plain_query() -> str:
            await _ensure_lightrag_storages(rag)
            return await rag.aquery(question, mode=mode)

        try:
            return asyncio.run(_plain_query())
        finally:
            _invalidate_personal_rag_cache()

    lightrag = rag.lightrag
    assert lightrag is not None, "LightRAG instance not initialised"

    async def _run():
        await _ensure_lightrag_storages(rag)
        result = await lightrag.aquery_llm(
            question,
            param=QueryParam(mode=_mode, include_references=True),
        )
        llm_response = result.get("llm_response", {})
        data = result.get("data", {})
        return {
            "answer": llm_response.get("content", ""),
            "chunks": data.get("chunks", []),
            "entities": data.get("entities", []),
            "relationships": data.get("relationships", []),
            "references": data.get("references", []),
        }

    try:
        return asyncio.run(_run())
    finally:
        _invalidate_personal_rag_cache()


# ---------------------------------------------------------------------------
# Noise content types that add no value to the knowledge graph
# ---------------------------------------------------------------------------
_SKIP_TYPES = frozenset({"footer", "page_number", "header"})


def _fix_image_paths(content_list: list, json_dir: Path) -> list:
    """Resolve relative img_path fields to absolute paths.

    MinerU stores image paths relative to the directory containing the
    content_list JSON file.  insert_content_list requires absolute paths.
    """
    fixed = []
    for item in content_list:
        if item.get("type") in _SKIP_TYPES:
            continue
        item = dict(item)  # shallow copy; do not mutate original
        rel = item.get("img_path")
        if rel:
            abs_path = (json_dir / rel).resolve()
            item["img_path"] = str(abs_path)
        fixed.append(item)
    return fixed


def reindex_from_cache(
    output_dir: str | Path | None = None,
    file_path: str | Path | None = None,
) -> None:
    """Re-build the index from existing MinerU parsed output (no re-parsing).

    When ``file_path`` is given (original document path, e.g. data/input/foo.pdf),
    only the cached parse results for that file are re-indexed.  Otherwise all
    ``*_content_list.json`` files under ``output_dir`` are processed.

    Useful after:
    - Clearing rag_storage (e.g. after an embedding dimension change)
    - Migrating to a new LLM / graph configuration
    """
    scan_dir = Path(output_dir) if output_dir else settings.output_dir
    if not scan_dir.exists():
        raise FileNotFoundError(f"Output dir not found: {scan_dir}")

    # Single-file reindex: locate the cached JSON for the given source file.
    if file_path is not None:
        src = Path(file_path)
        stem = src.stem
        doc_dir = scan_dir / stem
        if not doc_dir.exists():
            raise FileNotFoundError(
                f"No cached parse output found for '{stem}' under {scan_dir}. "
                "Run 'rag ingest' or 'rag parse' first."
            )
        json_files = [
            p for p in doc_dir.rglob("*_content_list.json")
            if "_content_list_v2" not in p.name
        ]
        if not json_files:
            raise FileNotFoundError(
                f"No *_content_list.json found under {doc_dir}."
            )
    else:
        # Find all content_list JSON files (exclude _v2 format)
        json_files = [
            p for p in scan_dir.rglob("*_content_list.json")
            if "_content_list_v2" not in p.name
        ]
        if not json_files:
            logger.warning(f"No *_content_list.json found under {scan_dir}")
            return

    logger.info(f"Found {len(json_files)} cached parse result(s) – rebuilding index")
    rag = get_rag()

    async def _rebuild():
        await _ensure_lightrag_storages(rag)
        for json_path in json_files:
            stem = json_path.stem.replace("_content_list", "")
            logger.info(f"Re-indexing: {stem}")
            raw: list = json.loads(json_path.read_text(encoding="utf-8"))
            content_list = _fix_image_paths(raw, json_path.parent)
            await rag.insert_content_list(
                content_list,
                file_path=stem,
            )
            logger.success(f"Re-indexed: {stem} ({len(content_list)} blocks)")

    try:
        asyncio.run(_rebuild())
    finally:
        _invalidate_personal_rag_cache()
    logger.success("Reindex complete.")


def clear_storage() -> None:
    """Delete the rag_storage directory (vectors + graph + cache).

    The parsed output in output/ is NOT touched, so you can reindex afterwards.
    """
    import shutil
    wd = settings.working_dir
    if wd.exists():
        shutil.rmtree(wd)
        logger.success(f"Deleted: {wd}")
    else:
        logger.info(f"Nothing to delete: {wd} does not exist")
    _invalidate_personal_rag_cache()


# ---------------------------------------------------------------------------
# Parse-only helpers (MinerU parsing without RAG indexing)
# ---------------------------------------------------------------------------

def _build_parser() -> RAGAnything:
    """Create a lightweight RAGAnything instance for parsing only (no LightRAG)."""
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    from raganything import RAGAnythingConfig
    config = RAGAnythingConfig(
        working_dir=str(settings.working_dir),
        parser_output_dir=str(settings.output_dir),
        parser=settings.parser,
        parse_method=settings.parse_method,
        enable_image_processing=True,
        enable_table_processing=True,
        enable_equation_processing=True,
    )
    return RAGAnything(
        config=config,
        llm_model_func=llm_model_func,
        vision_model_func=vision_model_func,
        embedding_func=build_embedding_func(),
    )


async def _aparse_file(rag: RAGAnything, file_path: Path) -> None:
    """Async: parse a single document (no indexing).

    Tries MinerU Cloud API first when configured; falls back to local MinerU
    on failure (if ``mineru_cloud_fallback_local`` is True).
    """
    out_dir = settings.output_dir / file_path.stem

    if settings.mineru_cloud_enabled and settings.mineru_cloud_api_key:
        try:
            await parse_file_via_cloud(file_path, out_dir)
            logger.success(f"Parsed (cloud): {file_path.name}")
            return
        except MineruCloudError as exc:
            if settings.mineru_cloud_fallback_local:
                logger.warning(
                    "MinerU Cloud 解析失败，降级到本地: {} — {}", file_path.name, exc
                )
            else:
                raise

    # Local MinerU (original path / fallback)
    await rag.parse_document(
        file_path=str(file_path),
        output_dir=str(out_dir),
        **_mineru_kwargs(),
    )
    logger.success(f"Parsed (local): {file_path.name}")


def parse_file(file_path: str | Path) -> None:
    """Parse a single document with MinerU and write output to output/parsed/ (no indexing)."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if file_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")

    logger.info(f"Parsing file: {file_path}")
    rag = _build_parser()
    asyncio.run(_aparse_file(rag, file_path))


def parse_folder(folder_path: str | Path) -> None:
    """Parse all supported documents in a folder with MinerU (no indexing)."""
    folder_path = Path(folder_path)
    if not folder_path.is_dir():
        raise NotADirectoryError(folder_path)

    files = [f for f in folder_path.rglob("*") if f.suffix.lower() in _SUPPORTED_SUFFIXES]
    if not files:
        logger.warning(f"No supported files found in {folder_path}")
        return

    logger.info(f"Found {len(files)} file(s) in {folder_path}")
    rag = _build_parser()

    async def _process_all():
        for f in files:
            await _aparse_file(rag, f)

    asyncio.run(_process_all())
    logger.success(f"Folder parsed: {folder_path} ({len(files)} files)")


# ---------------------------------------------------------------------------
# Course RAG (Phase 7) — single factory for PG-backed LightRAG + RAGAnything
# ---------------------------------------------------------------------------


def material_stable_doc_id(material_id: str) -> str:
    """Deterministic LightRAG document id for a platform material row."""
    return compute_mdhash_id(f"edu:material:{material_id}", prefix="doc-")


def _vision_for_course_rag() -> Any:
    if settings.enable_image_filter:
        return _filtered_vision_model_func
    return vision_model_func


async def get_course_rag_anything(course_id: str) -> RAGAnything:
    """Return a cached RAGAnything bound to this course's workspace (PG storages)."""
    workspace = course_id_to_workspace(course_id)
    async with _course_init_lock:
        if workspace in _course_cache:
            return _course_cache[workspace]

        ensure_postgres_env_from_database_url()

        # NetworkXStorage stores graph files locally, so each course needs its own
        # working directory to prevent graph data from being mixed across courses.
        # PGGraphStorage shares state via workspace= so a shared dir is fine.
        if settings.graph_storage == "NetworkXStorage":
            work = str(settings.working_dir / "course_graphs" / workspace)
        else:
            work = str(settings.working_dir / "course_pg_layout")
        Path(work).mkdir(parents=True, exist_ok=True)
        settings.output_dir.mkdir(parents=True, exist_ok=True)

        emb = build_embedding_func()
        lightrag = LightRAG(
            working_dir=work,
            workspace=workspace,
            llm_model_func=llm_model_func,
            embedding_func=emb,
            llm_model_max_async=settings.llm_max_async,
            embedding_func_max_async=settings.embedding_max_async,
            max_parallel_insert=settings.max_parallel_insert,
            kv_storage="PGKVStorage",
            vector_storage="PGVectorStorage",
            graph_storage=settings.graph_storage,
            doc_status_storage="PGDocStatusStorage",
        )
        await lightrag.initialize_storages()

        cfg = RAGAnythingConfig(
            working_dir=work,
            parser_output_dir=str(settings.output_dir),
            parser=settings.parser,
            parse_method=settings.parse_method,
            enable_image_processing=True,
            enable_table_processing=True,
            enable_equation_processing=True,
        )

        rag = RAGAnything(
            lightrag=lightrag,
            config=cfg,
            llm_model_func=llm_model_func,
            vision_model_func=_vision_for_course_rag(),
            embedding_func=emb,
        )
        init = await rag._ensure_lightrag_initialized()
        if not init.get("success"):
            raise RuntimeError(init.get("error") or "RAGAnything init failed for course")

        _course_cache[workspace] = rag
        logger.info("Course RAGAnything ready workspace={}", workspace)
        return rag

async def _finalize_course_rag(course_id: str) -> None:
    """Finalize course LightRAG storages on the current event loop when present."""
    ws = course_id_to_workspace(course_id)
    rag = _course_cache.get(ws)
    if not rag or not rag.lightrag:
        return
    await rag.lightrag.finalize_storages()


def _chunk_relevance_score(chunk: dict[str, Any], rank_index: int) -> float:
    for key in ("rerank_score", "score", "similarity"):
        v = chunk.get(key)
        if v is not None:
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
    dist = chunk.get("distance")
    if dist is not None:
        try:
            d = float(dist)
            return max(0.0, min(1.0, 1.0 / (1.0 + d)))
        except (TypeError, ValueError):
            pass
    return max(0.0, 1.0 - (rank_index * 0.02))


def _material_id_from_course_file_path(file_path: str | None) -> str | None:
    if not file_path:
        return None
    m = re.search(r"materials/[0-9a-fA-F-]{36}/([0-9a-fA-F-]{36})/", str(file_path))
    return m.group(1) if m else None


def _hits_from_aquery_chunks(chunks: list[Any], *, origin: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for i, ch in enumerate(chunks):
        if not isinstance(ch, dict):
            continue
        cid = str(ch.get("chunk_id") or "")
        text = str(ch.get("content") or "")
        fp = ch.get("file_path")
        mid = _material_id_from_course_file_path(str(fp) if fp is not None else "")
        hits.append(
            {
                "chunk_id": cid,
                "text": text,
                "metadata": {"material_id": mid, "file_path": fp},
                "relevance_score": _chunk_relevance_score(ch, i),
                "origin": origin,
            },
        )
    return hits


async def course_aquery_data(
    course_id: str,
    question: str,
    *,
    mode: str,
    top_k: int,
) -> dict[str, Any]:
    rag = await get_course_rag_anything(course_id)
    m = cast(
        "Literal['local', 'global', 'hybrid', 'naive', 'mix', 'bypass']",
        mode if mode in ("local", "global", "hybrid", "naive", "mix", "bypass") else "hybrid",
    )
    param = QueryParam(mode=m, top_k=top_k, chunk_top_k=top_k)
    assert rag.lightrag is not None
    return await rag.lightrag.aquery_data(question.strip(), param)


async def personal_aquery_data(
    question: str,
    *,
    mode: str,
    top_k: int,
) -> dict[str, Any]:
    rag = get_rag()
    await _ensure_lightrag_storages(rag)
    m = cast(
        "Literal['local', 'global', 'hybrid', 'naive', 'mix', 'bypass']",
        mode if mode in ("local", "global", "hybrid", "naive", "mix", "bypass") else "hybrid",
    )
    param = QueryParam(mode=m, top_k=top_k, chunk_top_k=top_k)
    assert rag.lightrag is not None
    return await rag.lightrag.aquery_data(question.strip(), param)


def course_retrieval_hits_sync(
    course_id: str,
    question: str,
    *,
    mode: str,
    top_k: int,
) -> list[dict[str, Any]]:
    async def _run() -> list[dict[str, Any]]:
        raw = await course_aquery_data(course_id, question, mode=mode, top_k=top_k)
        data = raw.get("data") or {}
        chunks = data.get("chunks") or []
        return _hits_from_aquery_chunks(chunks, origin="course")

    try:
        return asyncio.run(_run())
    finally:
        _invalidate_course_rag_cache_for(course_id)


def personal_retrieval_hits_sync(
    question: str,
    *,
    mode: str,
    top_k: int,
) -> list[dict[str, Any]]:
    async def _run() -> list[dict[str, Any]]:
        raw = await personal_aquery_data(question, mode=mode, top_k=top_k)
        data = raw.get("data") or {}
        chunks = data.get("chunks") or []
        return _hits_from_aquery_chunks(chunks, origin="personal")

    try:
        return asyncio.run(_run())
    finally:
        _invalidate_personal_rag_cache()


async def ingest_parsed_material_into_course_async(
    course_id: str,
    material_id: str,
    source_file: Path,
) -> int:
    """Insert already-parsed MinerU JSON (under output_dir / stem) into course LightRAG."""
    ensure_embedding_backend_reachable()

    stem = source_file.stem
    scan_dir = settings.output_dir / stem
    if not scan_dir.exists():
        raise FileNotFoundError(f"No parse output dir: {scan_dir}")
    json_files = [
        p
        for p in scan_dir.rglob("*_content_list.json")
        if "_content_list_v2" not in p.name
    ]
    if not json_files:
        raise FileNotFoundError(f"No *_content_list.json under {scan_dir}")

    try:
        rag = await get_course_rag_anything(course_id)
        doc_id = material_stable_doc_id(material_id)
        total = 0
        for json_path in json_files:
            sub_stem = json_path.stem.replace("_content_list", "")
            raw: list = json.loads(json_path.read_text(encoding="utf-8"))
            content_list = _fix_image_paths(raw, json_path.parent)
            await rag.insert_content_list(
                content_list,
                file_path=sub_stem,
                doc_id=doc_id,
            )
            total += len(content_list)

        # DocStatus storage can be briefly stale after inserts (especially for multimodal),
        # so poll for a short window to avoid false failures.
        poll_deadline_s = 60.0
        poll_sleep_s = 1.0
        poll_started = time.monotonic()
        st: dict[str, Any] = {}
        while True:
            st = await rag.get_document_processing_status(doc_id)
            if st.get("error") or st.get("fully_processed"):
                break
            if (time.monotonic() - poll_started) >= poll_deadline_s:
                break
            await asyncio.sleep(poll_sleep_s)

        if st.get("error"):
            raise RuntimeError(
                f"LightRAG document status lookup failed (doc_id={doc_id}): {st['error']}"
            )
        if total > 0 and not st.get("exists"):
            raise RuntimeError(
                f"LightRAG has no document record after ingest (doc_id={doc_id}, "
                f"content_blocks={total}). Check embedding and PostgreSQL connectivity."
            )
        if total > 0 and not st.get("fully_processed"):
            status = st.get("status")
            chunks_count = st.get("chunks_count")
            text_processed = st.get("text_processed")
            multimodal_processed = st.get("multimodal_processed")
            waited_ms = int((time.monotonic() - poll_started) * 1000)

            # Relaxed success criteria (prevents false FAILED when multimodal flag lags):
            # - doc exists and is marked processed
            # - we have chunks, and text processing is done
            try:
                chunks_int = int(chunks_count or 0)
            except (TypeError, ValueError):
                chunks_int = 0

            if (
                status == "processed"
                and chunks_int > 0
                and text_processed is True
                and st.get("exists")
            ):
                logger.warning(
                    "LightRAG doc_status not fully processed after ingest; continuing with relaxed success "
                    "(material_id={}, doc_id={}, waited_ms={}, fully_processed={}, text_processed={}, "
                    "multimodal_processed={}, status={!r}, chunks_count={}, embedding_mode={!r})",
                    material_id,
                    doc_id,
                    waited_ms,
                    st.get("fully_processed"),
                    text_processed,
                    multimodal_processed,
                    status,
                    chunks_count,
                    settings.embedding_mode,
                )
                return chunks_int

            raise RuntimeError(
                f"LightRAG did not fully index material {material_id} (doc_id={doc_id}): "
                f"waited_ms={waited_ms}, "
                f"text_processed={text_processed}, "
                f"multimodal_processed={multimodal_processed}, "
                f"fully_processed={st.get('fully_processed')}, "
                f"status={status!r}, chunks_count={chunks_count}. "
                "Possible cause: doc_status update lag, embedding backend failure, or PostgreSQL write issues."
            )

        return int(st.get("chunks_count") or total)
    finally:
        await _finalize_course_rag(course_id)


def ingest_parsed_material_into_course_sync(
    course_id: str,
    material_id: str,
    source_file: Path,
) -> int:
    try:
        return asyncio.run(
            ingest_parsed_material_into_course_async(course_id, material_id, source_file),
        )
    finally:
        _invalidate_course_rag_cache_for(course_id)


async def delete_material_course_async(course_id: str, material_id: str) -> None:
    try:
        rag = await get_course_rag_anything(course_id)
        if rag.lightrag is None:
            raise RuntimeError("Course RAGAnything has no LightRAG instance")
        doc_id = material_stable_doc_id(material_id)
        result = await rag.lightrag.adelete_by_doc_id(doc_id)
        if result.status not in ("success", "not_found"):
            raise RuntimeError(f"LightRAG delete failed: {result.status} {result.message}")
    finally:
        await _finalize_course_rag(course_id)


def delete_material_course_sync(course_id: str, material_id: str) -> None:
    try:
        asyncio.run(delete_material_course_async(course_id, material_id))
    finally:
        _invalidate_course_rag_cache_for(course_id)


async def get_lightrag_for_course(course_id: str) -> LightRAG:
    """Return the LightRAG instance for a course (PG storages + stable workspace)."""
    rag = await get_course_rag_anything(course_id)
    if rag.lightrag is None:
        raise RuntimeError("Course RAGAnything has no LightRAG instance")
    return rag.lightrag
