"""Course material pipeline: MinIO → engine.parse_file → LightRAG PG (workspace).

If PostgreSQL reports ``another operation is in progress`` during indexing, try
lowering ``MAX_PARALLEL_INSERT`` or ``EMBEDDING_MAX_ASYNC`` in ``rag_mvp`` settings.

When ``edu-rag-worker`` has started the persistent async loop (``worker_async_loop``),
parse and ingest run on that loop so LightRAG global locks stay on one event loop.
Otherwise parse uses ``engine.parse_file`` (``asyncio.run``) and ingest uses sync wrappers.
PARSED / INDEXING commits remain on the main thread between parse and ingest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotocoreConfig
import psycopg
from loguru import logger

from rag_mvp.config import settings
from rag_mvp.engine import (
    _aparse_file,
    _build_parser,
    _invalidate_course_rag_cache_for,
    delete_material_course_async,
    delete_material_course_sync,
    ingest_parsed_material_into_course_async,
    ingest_parsed_material_into_course_sync,
    parse_file,
)
from rag_mvp.worker_async_loop import is_worker_async_loop_started, run_worker_coroutine


def _s3_client():
    endpoint = os.environ["MINIO_ENDPOINT"].strip()
    if not endpoint.startswith("http"):
        use_ssl = os.environ.get("MINIO_USE_SSL", "true").lower() == "true"
        endpoint = ("https://" if use_ssl else "http://") + endpoint
    # Bypass any system HTTP proxy (e.g. Clash on 7890) for local MinIO connections.
    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"].strip(),
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"].strip(),
        region_name=os.environ.get("MINIO_REGION", "us-east-1").strip(),
        config=BotocoreConfig(proxies={}),  # disable system proxy (e.g. Clash) for local MinIO
    )


def _bucket() -> str:
    return os.environ["MINIO_BUCKET"].strip()


async def _parse_material_file_async(local_file: Path) -> None:
    """MinerU parse only (same behaviour as ``engine.parse_file``)."""
    logger.info("Parsing file: {}", local_file.name)
    rag = _build_parser()
    await _aparse_file(rag, local_file)


async def _ingest_parsed_material_worker_async(
    course_id: str,
    material_id: str,
    local_file: Path,
) -> int:
    """Ingest + same cache invalidation as ``ingest_parsed_material_into_course_sync``."""
    try:
        return await ingest_parsed_material_into_course_async(
            course_id,
            material_id,
            local_file,
        )
    finally:
        _invalidate_course_rag_cache_for(course_id)


async def _delete_material_course_worker_async(course_id: str, material_id: str) -> None:
    """Delete + same cache invalidation as ``delete_material_course_sync``."""
    try:
        await delete_material_course_async(course_id, material_id)
    finally:
        _invalidate_course_rag_cache_for(course_id)


def _parse_file_dispatch(local_file: Path) -> None:
    if is_worker_async_loop_started():
        run_worker_coroutine(_parse_material_file_async(local_file), timeout=None)
    else:
        parse_file(local_file)


def _ingest_parsed_dispatch(course_id: str, material_id: str, local_file: Path) -> int:
    if is_worker_async_loop_started():
        return run_worker_coroutine(
            _ingest_parsed_material_worker_async(course_id, material_id, local_file),
            timeout=None,
        )
    return ingest_parsed_material_into_course_sync(course_id, material_id, local_file)


def _delete_material_rag_dispatch(course_id: str, material_id: str) -> None:
    if is_worker_async_loop_started():
        run_worker_coroutine(
            _delete_material_course_worker_async(course_id, material_id),
            timeout=None,
        )
    else:
        delete_material_course_sync(course_id, material_id)


def download_object_to_path(minio_path: str, dest: Path) -> None:
    client = _s3_client()
    dest.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(_bucket(), minio_path, str(dest))


def _material_stale_seconds() -> int:
    return int(os.environ.get("RAG_MATERIAL_STALE_SEC", "1800"))


_OFFICE_SUFFIXES = frozenset({".ppt", ".pptx", ".doc", ".docx"})


def _convert_to_pdf(local_file: Path, out_dir: Path) -> Path:
    """Convert PPT/PPTX/DOC/DOCX to PDF via LibreOffice (required by MinerU). Returns PDF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(out_dir),
            str(local_file),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed (exit {result.returncode}): {result.stderr[:500]}"
        )
    pdf_path = out_dir / (local_file.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(
            f"LibreOffice ran successfully but PDF not found at {pdf_path}. "
            f"stdout: {result.stdout[:300]}"
        )
    return pdf_path


def _upload_object(local_path: Path, minio_path: str) -> None:
    """Upload a local file to MinIO at the given object key."""
    client = _s3_client()
    client.upload_file(str(local_path), _bucket(), minio_path)


def _preview_pdf_minio_key(minio_path: str) -> str:
    """Stable key for browser preview PDF (Office originals keep ``minio_path``)."""
    return str(Path(minio_path).parent / "preview.pdf")


def update_material_preview_pdf_status(
    conn: psycopg.Connection, material_id: str, status: str
) -> None:
    """``status``: NA | PENDING | READY | FAILED (Prisma enum)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE materials
            SET preview_pdf_status = %s::"MaterialPreviewPdfStatus",
                updated_at = NOW()
            WHERE id = %s::uuid AND is_deleted = false
            """,
            (status, material_id),
        )


def update_material_status(
    conn: psycopg.Connection,
    material_id: str,
    status: str,
    status_message: str | None = None,
    indexed_chunk_count: int | None = None,
    *,
    expect_status_in: tuple[str, ...] | None = None,
) -> bool:
    """Return True if a row was updated (for idempotency)."""
    sets = ["status = %s", "updated_at = NOW()"]
    args: list[Any] = [status]
    if status_message is not None:
        sets.append("status_message = %s")
        args.append(status_message)
    if indexed_chunk_count is not None:
        sets.append("indexed_chunk_count = %s")
        args.append(indexed_chunk_count)
    args.append(material_id)
    where = "id = %s::uuid AND is_deleted = false"
    if expect_status_in:
        placeholders = ", ".join(["%s"] * len(expect_status_in))
        where += f" AND status IN ({placeholders})"
        args.extend(expect_status_in)
    sql = f'UPDATE materials SET {", ".join(sets)} WHERE {where}'
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return (cur.rowcount or 0) > 0


def _claim_material_for_parse(
    conn: psycopg.Connection, material_id: str
) -> dict[str, Any] | None:
    """Atomically move material to PARSING when eligible; return row dict or None if skip."""
    stale = _material_stale_seconds()
    ex: tuple[Any, ...] | None = None
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE materials m
                SET status = 'PARSING', updated_at = NOW()
                FROM (
                    SELECT id FROM materials
                    WHERE id = %s::uuid AND is_deleted = false
                      AND (
                        status = 'UPLOADED'
                        OR (
                          status IN ('PARSING', 'INDEXING', 'PARSED')
                          AND updated_at < NOW() - (%s * INTERVAL '1 second')
                        )
                      )
                    FOR UPDATE SKIP LOCKED
                ) s
                WHERE m.id = s.id
                RETURNING m.course_id::text, m.minio_path, m.file_type
                """,
                (material_id, stale),
            )
            row = cur.fetchone()
            if row:
                return {
                    "course_id": row[0],
                    "minio_path": row[1],
                    "file_type": row[2],
                }
            cur.execute(
                """
                SELECT course_id::text, minio_path, file_type, status::text, is_deleted
                FROM materials WHERE id = %s::uuid
                """,
                (material_id,),
            )
            ex = cur.fetchone()
    if ex is None:
        logger.error("Material {} not found", material_id)
        return None
    if ex[4]:
        logger.warning("Material {} is deleted; skipping parse", material_id)
        return None
    status = ex[3]
    if status == "READY":
        logger.info("Material {} already READY (idempotent)", material_id)
        return None
    logger.info(
        "Material {} not claimable (status={}, not stale enough)",
        material_id,
        status,
    )
    return None


def process_parse_and_index(conn: psycopg.Connection, material_id: str) -> None:
    """DB is source of truth; parse via engine.parse_file; ingest via LightRAG insert only."""
    claimed = _claim_material_for_parse(conn, material_id)
    if not claimed:
        return

    course_id = claimed["course_id"]
    minio_path = claimed["minio_path"]
    file_type = claimed["file_type"]

    work_parent = Path(tempfile.mkdtemp(prefix="edu_mat_"))
    suffix = Path(minio_path).suffix or ".bin"
    local_file = work_parent / f"{material_id}{suffix}"

    try:
        download_object_to_path(minio_path, local_file)
        ft = file_type.lower()
        if ft == "image":
            raise ValueError("Image indexing is not supported for course materials in this phase")

        # Convert Office → PDF for MinerU and upload ``preview.pdf`` (original ``minio_path`` unchanged).
        if local_file.suffix.lower() in _OFFICE_SUFFIXES:
            logger.info(
                "Converting {} to PDF via LibreOffice (material {})",
                local_file.suffix,
                material_id,
            )
            try:
                pdf_file = _convert_to_pdf(local_file, work_parent / "pdf_out")
                preview_key = _preview_pdf_minio_key(minio_path)
                _upload_object(pdf_file, preview_key)
                with conn.transaction():
                    update_material_preview_pdf_status(conn, material_id, "READY")
            except Exception:
                with conn.transaction():
                    update_material_preview_pdf_status(conn, material_id, "FAILED")
                raise
            local_file = pdf_file
            ft = "pdf"
            logger.info("Conversion done → {} (preview at {})", pdf_file.name, preview_key)

        # Same parse stack as CLI `rag parse` (engine.parse_file), or worker persistent loop.
        _parse_file_dispatch(local_file)

        with conn.transaction():
            update_material_status(
                conn, material_id, "PARSED", None, expect_status_in=("PARSING",)
            )

        with conn.transaction():
            update_material_status(
                conn, material_id, "INDEXING", None, expect_status_in=("PARSED",)
            )

        n = _ingest_parsed_dispatch(course_id, material_id, local_file)

        with conn.transaction():
            ok = update_material_status(
                conn,
                material_id,
                "READY",
                None,
                indexed_chunk_count=n,
                expect_status_in=("INDEXING",),
            )
            if not ok:
                raise RuntimeError(
                    f"Material {material_id} lost INDEXING state before READY commit",
                )
        # Remove MinerU cache for this stem to limit disk growth (re-parse on re-ingest).
        stem_dir = settings.output_dir / material_id
        if stem_dir.exists():
            shutil.rmtree(stem_dir, ignore_errors=True)

        logger.success("Indexed material {} ({} chunks via LightRAG)", material_id, n)
    except Exception as exc:
        logger.exception("Material processing failed")
        shutil.rmtree(settings.output_dir / material_id, ignore_errors=True)
        with conn.transaction():
            update_material_status(
                conn,
                material_id,
                "FAILED",
                str(exc)[:2000],
            )
    finally:
        shutil.rmtree(work_parent, ignore_errors=True)


def process_delete_material(conn: psycopg.Connection, material_id: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT course_id::text, is_deleted
                FROM materials WHERE id = %s::uuid
                """,
                (material_id,),
            )
            row = cur.fetchone()
            if not row:
                logger.error("delete_material: material {} not found", material_id)
                return
            course_id, is_deleted = row[0], row[1]
            if not is_deleted:
                raise RuntimeError(
                    f"delete_material: material {material_id} expected is_deleted=true",
                )
    _delete_material_rag_dispatch(course_id, material_id)
    logger.info("Deleted LightRAG document for material {} (course {})", material_id, course_id)
