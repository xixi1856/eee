"""One-time script: create edu_lightrag database and enable pgvector."""
import os
import sys

import psycopg
from psycopg.conninfo import make_conninfo

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://edu:edu@localhost:5432/edu_platform"
)

# Build connection string to the maintenance "postgres" database
import re
dsn = re.sub(r"/[^/]+$", "/postgres", DATABASE_URL)

# psycopg requires autocommit for CREATE DATABASE
with psycopg.connect(dsn, autocommit=True) as conn:
    try:
        conn.execute("CREATE DATABASE edu_lightrag")
        print("Created database edu_lightrag")
    except psycopg.errors.DuplicateDatabase:
        print("Database edu_lightrag already exists")

# Connect to the new DB and enable vector extension
lightrag_dsn = re.sub(r"/[^/]+$", "/edu_lightrag", DATABASE_URL)
with psycopg.connect(lightrag_dsn) as conn:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    print("pgvector extension ready in edu_lightrag")
