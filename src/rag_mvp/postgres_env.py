"""Map DATABASE_URL into LightRAG PostgreSQL storage env vars (POSTGRES_*)."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse


def ensure_postgres_env_from_database_url() -> None:
    """Populate POSTGRES_* if missing so LightRAG PGKV/PGVector/PGDoc/PGGraph can connect."""
    if os.environ.get("POSTGRES_DATABASE") and os.environ.get("POSTGRES_USER"):
        return

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for course LightRAG (PostgreSQL storages)")

    parsed = urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres", "postgresql+psycopg", "postgresql+asyncpg"):
        raise RuntimeError(f"Unsupported DATABASE_URL scheme for LightRAG PG: {parsed.scheme!r}")

    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "") if parsed.password is not None else ""
    database = (parsed.path or "").lstrip("/")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    if not user or not database:
        raise RuntimeError("DATABASE_URL must include username and database name for LightRAG PG")

    os.environ["POSTGRES_USER"] = user
    os.environ["POSTGRES_PASSWORD"] = password
    os.environ["POSTGRES_DATABASE"] = database
    os.environ.setdefault("POSTGRES_HOST", host)
    os.environ.setdefault("POSTGRES_PORT", str(port))
