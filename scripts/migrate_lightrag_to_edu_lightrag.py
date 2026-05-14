"""Migrate lightrag_* tables from edu_platform ? edu_lightrag.

Steps:
  1. Initialize LightRAG storages in edu_lightrag (auto-creates schema)
  2. Copy all rows from edu_platform to edu_lightrag (handles vector columns)
  3. Drop lightrag_* tables from edu_platform
  4. Print prisma migrate dev suggestion

Run with:  uv run python scripts/migrate_lightrag_to_edu_lightrag.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make sure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC_DSN = "postgresql://edu:edu@localhost:5432/edu_platform"
DST_DSN = "postgresql://edu:edu@localhost:5432/edu_lightrag"

LIGHTRAG_TABLES = [
    "lightrag_doc_full",
    "lightrag_doc_status",
    "lightrag_doc_chunks",
    "lightrag_entity_chunks",
    "lightrag_relation_chunks",
    "lightrag_full_entities",
    "lightrag_full_relations",
    "lightrag_llm_cache",
    "lightrag_vdb_chunks",
    "lightrag_vdb_entity",
    "lightrag_vdb_relation",
]

# Tables that have a vector column (content_vector); needs special handling
VECTOR_TABLES = {"lightrag_vdb_chunks", "lightrag_vdb_entity", "lightrag_vdb_relation"}


# ---------------------------------------------------------------------------
# Step 1: Initialize LightRAG in edu_lightrag (creates tables automatically)
# ---------------------------------------------------------------------------

async def init_lightrag_storages() -> None:
    """Let LightRAG create its own schema in edu_lightrag via initialize_storages()."""
    print("? Step 1: Initializing LightRAG storages in edu_lightrag ?")

    # Point LightRAG at the destination DB
    os.environ["LIGHTRAG_PG_DSN"] = DST_DSN
    os.environ["DATABASE_URL"] = SRC_DSN  # keep app DB separate

    from rag_mvp.postgres_env import ensure_postgres_env_from_database_url
    ensure_postgres_env_from_database_url()

    from rag_mvp.engine import _lightrag_constructor_extras, _lightrag_insertion_tuning_kwargs
    from rag_mvp.llm import build_embedding_func, llm_model_func, vision_model_func
    from rag_mvp.config import settings
    from lightrag import LightRAG

    work = str(settings.working_dir / "course_neo4j_layout")
    Path(work).mkdir(parents=True, exist_ok=True)

    emb = build_embedding_func()
    # Use a dummy workspace just to trigger initialization
    lightrag = LightRAG(
        working_dir=work,
        workspace="__migration_init__",
        llm_model_func=llm_model_func,
        embedding_func=emb,
        kv_storage="PGKVStorage",
        vector_storage="PGVectorStorage",
        graph_storage="Neo4JStorage",
        doc_status_storage="PGDocStatusStorage",
        **_lightrag_insertion_tuning_kwargs(),
        **_lightrag_constructor_extras(),
    )
    await lightrag.initialize_storages()
    print("   [OK] Tables created in edu_lightrag")


# ---------------------------------------------------------------------------
# Step 2: Copy data (with vector column handling)
# ---------------------------------------------------------------------------

def _get_columns(conn, table: str) -> list[tuple[str, str]]:
    """Return list of (column_name, data_type) for the given table."""
    cur = conn.execute(
        """
        SELECT column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return cur.fetchall()


def copy_table(src_conn, dst_conn, table: str) -> int:
    """Copy all rows from src to dst for one table. Returns row count copied."""
    cols_info = _get_columns(src_conn, table)
    if not cols_info:
        print(f"   skip {table}: not found in source")
        return 0

    col_names = [c[0] for c in cols_info]
    col_types = {c[0]: c[1] for c in cols_info}

    # Cast non-scalar types to text on SELECT so psycopg can handle them uniformly
    CAST_TO_TEXT = {"vector", "jsonb", "json", "_jsonb", "_json"}
    select_parts = []
    for name in col_names:
        if col_types[name] in CAST_TO_TEXT:
            select_parts.append(f"{name}::text AS {name}")
        else:
            select_parts.append(name)
    select_sql = f"SELECT {', '.join(select_parts)} FROM {table}"

    # Cast back to the original type on INSERT
    placeholders = []
    for name in col_names:
        if col_types[name] == "vector":
            placeholders.append("%s::vector")
        elif col_types[name] in ("jsonb", "_jsonb"):
            placeholders.append("%s::jsonb")
        elif col_types[name] in ("json", "_json"):
            placeholders.append("%s::json")
        else:
            placeholders.append("%s")
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(col_names)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT DO NOTHING"
    )

    cur = src_conn.execute(select_sql)
    rows = cur.fetchall()
    if not rows:
        print(f"   - {table}: 0 rows (empty)")
        return 0

    with dst_conn.cursor() as wcur:
        wcur.executemany(insert_sql, rows)
    dst_conn.commit()
    print(f"   [OK] {table}: {len(rows)} row(s) copied")
    return len(rows)


def copy_all_data() -> None:
    import psycopg

    print("? Step 2: Copying data from edu_platform ? edu_lightrag ?")
    with psycopg.connect(SRC_DSN) as src, psycopg.connect(DST_DSN) as dst:
        total = 0
        for table in LIGHTRAG_TABLES:
            total += copy_table(src, dst, table)
    print(f"   ? Total rows copied: {total}")


# ---------------------------------------------------------------------------
# Step 3: Drop lightrag_* tables from edu_platform
# ---------------------------------------------------------------------------

def drop_lightrag_from_edu_platform() -> None:
    import psycopg

    print("? Step 3: Dropping lightrag_* tables from edu_platform ?")
    drop_sql = "DROP TABLE IF EXISTS " + ", ".join(LIGHTRAG_TABLES) + " CASCADE"
    with psycopg.connect(SRC_DSN, autocommit=True) as conn:
        conn.execute(drop_sql)
    print("   ? Dropped: " + ", ".join(LIGHTRAG_TABLES))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    asyncio.run(init_lightrag_storages())
    copy_all_data()
    drop_lightrag_from_edu_platform()

    print()
    print("[DONE] Migration complete!")
    print()
    print("Next steps:")
    print("  cd edu-platform && npx prisma migrate dev")
    print("  (should now run without 'Drift detected' error)")


if __name__ == "__main__":
    main()
