-- Phase 7 convergence: course vectors live in LightRAG LIGHTRAG_* tables (workspace), not rag_*.

DROP TABLE IF EXISTS "rag_chunks" CASCADE;
DROP TABLE IF EXISTS "rag_entities" CASCADE;
DROP TABLE IF EXISTS "rag_relationships" CASCADE;
