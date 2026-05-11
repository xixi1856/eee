-- Phase 7: global RAG tables with HASH(course_id) partitioning + pgvector.
-- Replaces per-course dynamic tables course_{uuid_hex}_chunks.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- rag_chunks
-- ---------------------------------------------------------------------------
CREATE TABLE "rag_chunks" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "course_id" UUID NOT NULL,
    "material_id" UUID NOT NULL,
    "chunk_index" INTEGER NOT NULL,
    "chunk_text" TEXT NOT NULL,
    "embedding" vector(1024) NOT NULL,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "rag_chunks_pkey" PRIMARY KEY ("id", "course_id")
) PARTITION BY HASH ("course_id");

DO $$
DECLARE i int;
BEGIN
  FOR i IN 0..7 LOOP
    EXECUTE format(
      'CREATE TABLE rag_chunks_p%s PARTITION OF rag_chunks FOR VALUES WITH (MODULUS 8, REMAINDER %s);',
      i, i
    );
  END LOOP;
END $$;

CREATE INDEX "rag_chunks_course_material_idx" ON "rag_chunks" ("course_id", "material_id");
-- Optional: add HNSW/IVFFlat in ops after data volume warrants it (migration stays portable).

-- ---------------------------------------------------------------------------
-- rag_entities (reserved for graph / LightRAG ETL; not populated by worker v1)
-- ---------------------------------------------------------------------------
CREATE TABLE "rag_entities" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "course_id" UUID NOT NULL,
    "entity_name" TEXT,
    "entity_type" TEXT,
    "description" TEXT,
    "embedding" vector(1024),
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "rag_entities_pkey" PRIMARY KEY ("id", "course_id")
) PARTITION BY HASH ("course_id");

DO $$
DECLARE i int;
BEGIN
  FOR i IN 0..7 LOOP
    EXECUTE format(
      'CREATE TABLE rag_entities_p%s PARTITION OF rag_entities FOR VALUES WITH (MODULUS 8, REMAINDER %s);',
      i, i
    );
  END LOOP;
END $$;

CREATE INDEX "rag_entities_course_idx" ON "rag_entities" ("course_id");

-- ---------------------------------------------------------------------------
-- rag_relationships
-- ---------------------------------------------------------------------------
CREATE TABLE "rag_relationships" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "course_id" UUID NOT NULL,
    "source_entity_id" UUID NOT NULL,
    "target_entity_id" UUID NOT NULL,
    "relationship_type" TEXT NOT NULL,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "rag_relationships_pkey" PRIMARY KEY ("id", "course_id")
) PARTITION BY HASH ("course_id");

DO $$
DECLARE i int;
BEGIN
  FOR i IN 0..7 LOOP
    EXECUTE format(
      'CREATE TABLE rag_relationships_p%s PARTITION OF rag_relationships FOR VALUES WITH (MODULUS 8, REMAINDER %s);',
      i, i
    );
  END LOOP;
END $$;

CREATE INDEX "rag_relationships_course_idx" ON "rag_relationships" ("course_id");
