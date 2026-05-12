"""Redis Stream consumer for course material RAG tasks (XREADGROUP + XACK + XAUTOCLAIM)."""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

import psycopg
import redis
from dotenv import load_dotenv
from loguru import logger

from rag_mvp.db import connect_sync
from rag_mvp.material_processor import (
    process_convert_preview,
    process_delete_material,
    process_index_only,
    process_parse_and_index,
    process_repair_preview,
)
from rag_mvp.worker_async_loop import start_worker_async_loop, stop_worker_async_loop


def _stream_name() -> str:
    return os.environ.get("RAG_TASK_STREAM_NAME", "edu:rag:tasks:stream").strip()


def _group_name() -> str:
    return os.environ.get("RAG_TASK_STREAM_GROUP", "edu-rag-workers").strip()


def _consumer_name() -> str:
    return os.environ.get("RAG_TASK_CONSUMER_NAME", f"edu-rag-{os.getpid()}").strip()


def _claim_idle_ms() -> int:
    return int(os.environ.get("RAG_STREAM_CLAIM_IDLE_MS", "300000"))


def _ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        r.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        logger.info("Created stream consumer group {} on {}", group, stream)
    except redis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


def _ensure_conn_alive(conn: psycopg.Connection) -> psycopg.Connection:
    """Return a live DB connection, reconnecting if the current one is broken.

    Long-running tasks (e.g. LibreOffice PPT→PDF conversion) can outlast the
    server's idle-connection timeout.  A dead connection would leave materials
    stuck in PENDING/UPLOADED after a successful MinIO upload because the DB
    status update silently fails and the task is ACKed with no retry.
    """
    try:
        # Lightweight ping to verify the socket is alive.
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        # Roll back any implicit transaction started by the ping so the next
        # task starts in a clean state (rollback is a no-op when nothing was
        # modified, so this is always safe).
        conn.rollback()
        return conn
    except Exception:
        logger.warning("DB connection lost; attempting to reconnect before next task")
        try:
            conn.close()
        except Exception:
            pass
        return connect_sync(autocommit=False)


def _coerce_field_dict(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, (list, tuple)):
        out: dict[str, str] = {}
        it = iter(raw)
        for k in it:
            v = next(it, None)
            if v is not None:
                out[str(k)] = str(v)
        return out
    return {}


def _parse_autoclaim_messages(resp: Any) -> list[tuple[str, dict[str, str]]]:
    """Parse XAUTOCLAIM RESP2: [cursor, [[id, [k,v,...]], ...]]."""
    if not isinstance(resp, (list, tuple)) or len(resp) < 2:
        return []
    msgs = resp[1]
    if not isinstance(msgs, list):
        return []
    out: list[tuple[str, dict[str, str]]] = []
    for item in msgs:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        msg_id = str(item[0])
        out.append((msg_id, _coerce_field_dict(item[1])))
    return out


def _parse_bool_field(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if not s:
        return default
    return s in ("1", "true", "yes", "on")


def _process_one(conn: Any, fields: dict[str, str]) -> None:
    op = fields.get("operation")
    text_only = _parse_bool_field(fields.get("text_only"), default=True)
    skip_kg = _parse_bool_field(fields.get("skip_kg"), default=True)
    if op == "assignment.generate":
        from rag_mvp.assignment_gen import generate_assignment
        import json as _json
        assignment_id = (fields.get("assignment_id") or "").strip()
        course_id = (fields.get("course_id") or "").strip()
        teacher_request = fields.get("teacher_request", "")
        if not assignment_id or not course_id:
            raise ValueError("missing assignment_id or course_id")
        structured_params_raw = (fields.get("structured_params") or "").strip()
        structured_params = _json.loads(structured_params_raw) if structured_params_raw else None
        generate_assignment(assignment_id, course_id, teacher_request, conn, structured_params=structured_params)
        return
    material_id = (fields.get("material_id") or "").strip()
    if not material_id:
        raise ValueError("missing material_id")
    if op == "parse_and_index":
        process_parse_and_index(conn, material_id, text_only=text_only, skip_kg=skip_kg)
    elif op == "index_only":
        process_index_only(conn, material_id, text_only=text_only, skip_kg=skip_kg)
    elif op == "delete_material":
        process_delete_material(conn, material_id)
    elif op == "repair_preview":
        process_repair_preview(conn, material_id)
    elif op == "convert_preview":
        process_convert_preview(conn, material_id, text_only=text_only, skip_kg=skip_kg)
    else:
        raise ValueError(f"unknown operation: {op!r}")


def _handle_entries(
    conn: Any,
    r: redis.Redis,
    stream: str,
    group: str,
    entries: list[tuple[str, dict[str, str]]],
) -> psycopg.Connection:
    """Process stream entries and return the (possibly-reconnected) DB connection."""
    for msg_id, raw_fields in entries:
        fields = {str(k): str(v) for k, v in raw_fields.items()}
        # Ensure the DB connection is alive before each task.  A long previous
        # task (e.g. soffice conversion) may have caused the server to close the
        # idle socket, which would leave the material stuck in PENDING/UPLOADED.
        conn = _ensure_conn_alive(conn)
        try:
            _process_one(conn, fields)
            r.xack(stream, group, msg_id)
        except ValueError as exc:
            logger.error("Invalid or poison task stream_id={}: {}", msg_id, exc)
            r.xack(stream, group, msg_id)
        except Exception:
            logger.exception("Task failed stream_id={}", msg_id)
            # ACK so the message leaves the PEL immediately; material/index paths persist FAILED in DB.
            # Retries: index_only or re-enqueue (see material_processor / assignment_gen).
            try:
                r.xack(stream, group, msg_id)
            except redis.ResponseError:
                logger.exception("XACK failed stream_id={}", msg_id)
    return conn


def main() -> None:
    # Load root-level .env first, then edu-platform/.env as fallback (override=False keeps
    # already-set values, so system env vars always win).
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.abspath(os.path.join(_here, "..", ".."))
    load_dotenv(os.path.join(_root, ".env"))
    load_dotenv(os.path.join(_root, "edu-platform", ".env"))
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        logger.error("REDIS_URL is required")
        sys.exit(1)
    stream = _stream_name()
    group = _group_name()
    consumer = _consumer_name()
    idle_ms = _claim_idle_ms()
    r = redis.from_url(redis_url, decode_responses=True)
    conn = connect_sync(autocommit=False)
    stop = False

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    _ensure_group(r, stream, group)
    start_worker_async_loop()
    logger.info(
        "edu-rag-worker stream={} group={} consumer={} claim_idle_ms={} persistent_async_loop=on",
        stream,
        group,
        consumer,
        idle_ms,
    )
    try:
        while not stop:
            try:
                resp = r.execute_command(
                    "XAUTOCLAIM",
                    stream,
                    group,
                    consumer,
                    str(idle_ms),
                    "0-0",
                    "COUNT",
                    25,
                )
                claimed = _parse_autoclaim_messages(resp)
                if claimed:
                    conn = _handle_entries(conn, r, stream, group, claimed)
                msgs = r.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream: ">"},
                    count=5,
                    block=5000,
                )
                if msgs:
                    for _sname, entries in msgs:
                        if entries:
                            conn = _handle_entries(conn, r, stream, group, entries)
            except redis.ConnectionError:
                logger.exception("Redis connection error")
            except Exception:
                logger.exception("Worker loop error")
    finally:
        stop_worker_async_loop()
        conn.close()
        logger.info("edu-rag-worker stopped")


if __name__ == "__main__":
    main()
