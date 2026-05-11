"""Redis Stream consumer for course material RAG tasks (XREADGROUP + XACK + XAUTOCLAIM)."""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

import redis
from dotenv import load_dotenv
from loguru import logger

from rag_mvp.db import connect_sync
from rag_mvp.material_processor import process_delete_material, process_parse_and_index


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


def _process_one(conn: Any, fields: dict[str, str]) -> None:
    op = fields.get("operation")
    material_id = (fields.get("material_id") or "").strip()
    if not material_id:
        raise ValueError("missing material_id")
    if op == "parse_and_index":
        process_parse_and_index(conn, material_id)
    elif op == "delete_material":
        process_delete_material(conn, material_id)
    else:
        raise ValueError(f"unknown operation: {op!r}")


def _handle_entries(
    conn: Any,
    r: redis.Redis,
    stream: str,
    group: str,
    entries: list[tuple[str, dict[str, str]]],
) -> None:
    for msg_id, raw_fields in entries:
        fields = {str(k): str(v) for k, v in raw_fields.items()}
        try:
            _process_one(conn, fields)
            r.xack(stream, group, msg_id)
        except ValueError as exc:
            logger.error("Invalid or poison task stream_id={}: {}", msg_id, exc)
            r.xack(stream, group, msg_id)
        except Exception:
            logger.exception("Task failed stream_id={}", msg_id)


def main() -> None:
    load_dotenv()
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
    logger.info(
        "edu-rag-worker stream={} group={} consumer={} claim_idle_ms={}",
        stream,
        group,
        consumer,
        idle_ms,
    )
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
                _handle_entries(conn, r, stream, group, claimed)
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
                        _handle_entries(conn, r, stream, group, entries)
        except redis.ConnectionError:
            logger.exception("Redis connection error")
        except Exception:
            logger.exception("Worker loop error")

    conn.close()
    logger.info("edu-rag-worker stopped")


if __name__ == "__main__":
    main()
