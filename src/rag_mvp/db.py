"""Shared synchronous PostgreSQL connection for Prisma-managed tables (e.g. materials)."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg


def _database_url_for_psycopg(dsn: str) -> str:
    """Prisma adds ``?schema=public``; libpq/psycopg rejects unknown URI query keys."""
    parsed = urlparse(dsn.strip())
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    schema_name: str | None = None
    out: list[tuple[str, str]] = []
    for key, val in pairs:
        if key == "schema":
            schema_name = val or "public"
            continue
        out.append((key, val))
    if schema_name and schema_name != "public":
        out.append(("options", f"-csearch_path={schema_name}"))
    query = urlencode(out) if out else ""
    return urlunparse(parsed._replace(query=query))


def connect_sync(*, autocommit: bool = False) -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg.connect(_database_url_for_psycopg(dsn), autocommit=autocommit)
