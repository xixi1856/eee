"""Per-course LightRAG — public entry points; implementation lives in rag_mvp.engine."""

from __future__ import annotations

# Re-export for callers that still import rag_mvp.course_lightrag (Phase 7 single factory).
from rag_mvp.engine import (
    course_aquery_data,
    course_retrieval_hits_sync,
    delete_material_course_async,
    delete_material_course_sync,
    get_course_rag_anything,
    get_lightrag_for_course,
    ingest_parsed_material_into_course_async,
    ingest_parsed_material_into_course_sync,
    material_stable_doc_id,
    personal_retrieval_hits_sync,
)

__all__ = [
    "course_aquery_data",
    "course_retrieval_hits_sync",
    "delete_material_course_async",
    "delete_material_course_sync",
    "get_course_rag_anything",
    "get_lightrag_for_course",
    "ingest_parsed_material_into_course_async",
    "ingest_parsed_material_into_course_sync",
    "material_stable_doc_id",
    "personal_retrieval_hits_sync",
]
