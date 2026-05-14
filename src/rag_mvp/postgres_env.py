"""Map DATABASE_URL (or LIGHTRAG_PG_DSN) into LightRAG PostgreSQL storage env vars (POSTGRES_*)."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse


def ensure_postgres_env_from_database_url() -> None:
    """Populate POSTGRES_* if missing so LightRAG PGKV/PGVector/PGDoc/PGGraph can connect.

    Priority: LIGHTRAG_PG_DSN > DATABASE_URL.
    Set LIGHTRAG_PG_DSN to a dedicated database (e.g. edu_lightrag) so that the
    lightrag_* tables are isolated from the Prisma-managed edu_platform schema,
    eliminating migration drift.
    """
    if os.environ.get("POSTGRES_DATABASE") and os.environ.get("POSTGRES_USER"):
        return

    # Prefer an explicit LightRAG DSN; fall back to the shared app DATABASE_URL.
    dsn = os.environ.get("LIGHTRAG_PG_DSN", "").strip()
    if not dsn:
        fallback = os.environ.get("DATABASE_URL", "").strip()
        if not fallback:
            raise RuntimeError(
                "Either LIGHTRAG_PG_DSN or DATABASE_URL is required for LightRAG (PostgreSQL storages)"
            )
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "LIGHTRAG_PG_DSN is not set; falling back to DATABASE_URL for LightRAG storage. "
            "LightRAG may connect to the wrong database (edu_platform instead of edu_lightrag). "
            "Set LIGHTRAG_PG_DSN=postgresql://.../edu_lightrag to silence this warning."
        )
        dsn = fallback

    parsed = urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres", "postgresql+psycopg", "postgresql+asyncpg"):
        raise RuntimeError(f"Unsupported scheme for LightRAG PG DSN: {parsed.scheme!r}")

    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "") if parsed.password is not None else ""
    database = (parsed.path or "").lstrip("/")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    if not user or not database:
        raise RuntimeError(
            "LightRAG PG DSN must include username and database name"
        )

    os.environ["POSTGRES_USER"] = user
    os.environ["POSTGRES_PASSWORD"] = password
    os.environ["POSTGRES_DATABASE"] = database
    os.environ.setdefault("POSTGRES_HOST", host)
    os.environ.setdefault("POSTGRES_PORT", str(port))
