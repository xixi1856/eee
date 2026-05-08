"""RAGAnything engine - initialisation and high-level ingest/query helpers."""

import asyncio
import json
from pathlib import Path

from lightrag import LightRAG
from loguru import logger
from raganything import RAGAnything, RAGAnythingConfig

from .config import settings
from .llm import embedding_func, llm_model_func, vision_model_func, _filtered_vision_model_func

# ---------------------------------------------------------------------------
# Metadata helpers – persist embedding config so mismatches are caught early
# ---------------------------------------------------------------------------
_METADATA_FILE = "rag_storage/.metadata.json"


def _write_metadata() -> None:
    path = Path(_METADATA_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
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


def _build_rag() -> RAGAnything:
    """Instantiate and return a configured RAGAnything instance.

    A LightRAG instance is created and its storages initialised before being
    handed to RAGAnything.  This ensures aquery() works even when no documents
    have been ingested yet.
    """
    _check_metadata()  # abort early if embedding dim has changed
    settings.working_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    lightrag = LightRAG(
        working_dir=str(settings.working_dir),
        llm_model_func=llm_model_func,
        embedding_func=embedding_func,
        llm_model_max_async=settings.llm_max_async,
        embedding_func_max_async=settings.embedding_max_async,
        max_parallel_insert=settings.max_parallel_insert,
    )
    asyncio.run(lightrag.initialize_storages())
    _write_metadata()  # persist / refresh after successful init

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
        embedding_func=embedding_func,
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


async def _aingest_file(rag: RAGAnything, file_path: Path) -> None:
    """Async: parse and index a single document file."""
    out_dir = str(settings.output_dir / file_path.stem)
    await rag.process_document_complete(
        file_path=str(file_path),
        output_dir=out_dir,
        **_mineru_kwargs(),
    )
    logger.success(f"Ingested: {file_path.name}")


def ingest_file(file_path: str | Path) -> None:
    """Parse and index a single document file."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if file_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")

    logger.info(f"Ingesting file: {file_path}")
    rag = get_rag()
    asyncio.run(_aingest_file(rag, file_path))


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
        for f in files:
            await _aingest_file(rag, f)

    asyncio.run(_process_all())
    logger.success(f"Folder ingested: {folder_path} ({len(files)} files)")


def query(question: str, mode: str = "hybrid", with_refs: bool = False) -> str | dict:
    """Run a RAG query and return the answer.

    Args:
        question: Natural-language question.
        mode: Retrieval mode (hybrid / local / global / naive).
        with_refs: When True, returns a dict with keys ``answer``,
            ``chunks``, ``entities``, ``relationships`` and ``references``
            instead of a plain string.
    """
    from typing import Literal, cast
    from lightrag import QueryParam

    _mode = cast(
        "Literal['local', 'global', 'hybrid', 'naive', 'mix', 'bypass']",
        mode,
    )
    rag = get_rag()
    if not with_refs:
        return asyncio.run(rag.aquery(question, mode=mode))

    lightrag = rag.lightrag
    assert lightrag is not None, "LightRAG instance not initialised"

    async def _run():
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

    return asyncio.run(_run())


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

    asyncio.run(_rebuild())
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
        embedding_func=embedding_func,
    )


async def _aparse_file(rag: RAGAnything, file_path: Path) -> None:
    """Async: parse a single document with MinerU (no indexing)."""
    out_dir = str(settings.output_dir / file_path.stem)
    await rag.parse_document(
        file_path=str(file_path),
        output_dir=out_dir,
        **_mineru_kwargs(),
    )
    logger.success(f"Parsed: {file_path.name}")


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
