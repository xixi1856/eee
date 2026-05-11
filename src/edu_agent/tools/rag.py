"""RAG knowledge-base tools.

Toolset: rag
Tools: knowledge_query, generate_quiz, ingest_document, build_mindmap
"""

from __future__ import annotations

import json
import logging
from typing import Any

from edu_agent.runtime_context import get_current_runtime
from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SCHEMA_KNOWLEDGE_QUERY = {
    "name": "knowledge_query",
    "description": (
        "从知识库中检索信息，回答关于已导入文档的任何知识性问题。"
        "在回答概念、原理、定义、事实类问题时应首先调用此工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要查询的自然语言问题",
            },
            "mode": {
                "type": "string",
                "enum": ["hybrid", "local", "global", "naive"],
                "description": (
                    "检索模式："
                    "hybrid（默认，综合模式）、"
                    "local（精确段落匹配）、"
                    "global（全局主题推断）、"
                    "naive（纯向量检索）"
                ),
            },
            "sources": {
                "description": (
                    "必填。字符串：personal | course | all | enrolled_courses；或数组：仅含 course/personal "
                    "（如 [\"course\",\"personal\"] 等价于 all）。"
                    "未绑定单门课程（问答中心）时必须使用 personal 或 enrolled_courses；"
                    "enrolled_courses 会在用户有权限的全部课程知识库中检索。"
                ),
                "oneOf": [
                    {
                        "type": "string",
                        "enum": ["personal", "course", "all", "enrolled_courses"],
                    },
                    {
                        "type": "array",
                        "items": {"type": "string", "enum": ["course", "personal"]},
                        "minItems": 1,
                        "maxItems": 2,
                    },
                ],
            },
            "top_k": {
                "type": "integer",
                "description": "每个来源返回的最大片段数（默认 5，范围 1–20）",
            },
        },
        "required": ["question", "sources"],
    },
}

_SCHEMA_GENERATE_QUIZ = {
    "name": "generate_quiz",
    "description": (
        "根据知识库内容生成练习题目，支持指定数量和题型。"
        "当用户要求练习、做题、出题或测验时调用此工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "生成题目数量（默认 5，最多 20）",
            },
            "question_type": {
                "type": "string",
                "enum": [
                    "single_choice",
                    "multi_choice",
                    "fill_blank",
                    "short_answer",
                    "mixed",
                ],
                "description": "题型：单选、多选、填空、简答、混合（默认混合）",
            },
        },
        "required": [],
    },
}

_SCHEMA_INGEST_DOCUMENT = {
    "name": "ingest_document",
    "description": (
        "将已解析的 Markdown 文件或目录导入 RAG 知识库，使其可被检索。"
        "文档必须先经过 parse_document 解析后才能导入。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "待导入的 Markdown 文件路径或目录路径",
            },
        },
        "required": ["path"],
    },
}

_SCHEMA_BUILD_MINDMAP = {
    "name": "build_mindmap",
    "description": (
        "根据指定的 Markdown 文件或目录生成思维导图 HTML 文件。"
        "当用户要求生成思维导图、知识结构图、知识树时调用此工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Markdown 文件路径或包含 Markdown 文件的目录路径",
            },
            "refine": {
                "type": "boolean",
                "description": "是否使用 LLM 精炼（输出更丰富，但速度较慢）",
            },
        },
        "required": ["source"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _fetch_material_titles(material_ids: set[str]) -> dict[str, str]:
    """Resolve original_filename for UUID material ids (DATABASE_URL)."""
    import os

    if not material_ids:
        return {}
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return {}
    try:
        import psycopg
    except ImportError:
        return {}
    out: dict[str, str] = {}
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for mid in material_ids:
                    cur.execute(
                        "SELECT original_filename FROM materials WHERE id = %s::uuid",
                        (mid,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        out[mid] = str(row[0])
    except Exception:
        logger.exception("material title lookup failed")
    return out


def _sync_verify_and_query_course(
    user_id: str,
    course_id: str,
    question: str,
    top_k: int,
    mode: str,
) -> list[dict[str, Any]]:
    import os

    import httpx
    from rag_mvp.course_lightrag import course_retrieval_hits_sync

    base = os.environ.get("EDU_PLATFORM_BASE_URL", "").rstrip("/")
    key = os.environ.get("EDU_PLATFORM_INTERNAL_API_KEY", "").strip()
    if not base or len(key) < 16:
        raise RuntimeError(
            "EDU_PLATFORM_BASE_URL and EDU_PLATFORM_INTERNAL_API_KEY (16+ chars) are required for course RAG",
        )
    with httpx.Client(timeout=120.0) as client:
        r = client.get(
            f"{base}/api/v1/internal/course-rag-access",
            params={"course_id": course_id, "user_id": user_id},
            headers={"X-Internal-Key": key},
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("access"):
            raise PermissionError("User has no access to this course")
    return course_retrieval_hits_sync(course_id, question, mode=mode, top_k=top_k)


def _sync_list_enrolled_course_ids(user_id: str) -> list[str]:
    """Platform internal: course UUIDs the agent user may RAG (enrollments + teaching)."""
    import os

    import httpx

    base = os.environ.get("EDU_PLATFORM_BASE_URL", "").rstrip("/")
    key = os.environ.get("EDU_PLATFORM_INTERNAL_API_KEY", "").strip()
    if not base or len(key) < 16:
        raise RuntimeError(
            "EDU_PLATFORM_BASE_URL and EDU_PLATFORM_INTERNAL_API_KEY (16+ chars) are required",
        )
    with httpx.Client(timeout=60.0) as client:
        r = client.get(
            f"{base}/api/v1/internal/enrolled-courses-rag",
            params={"user_id": user_id},
            headers={"X-Internal-Key": key},
        )
        r.raise_for_status()
        body = r.json()
    raw = body.get("course_ids")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _normalize_knowledge_sources(raw: Any) -> tuple[str | None, str | None]:
    """Map ``sources`` tool arg to personal|course|all|enrolled_courses."""
    if raw is None:
        return None, "缺少必要参数：sources（personal | course | all | enrolled_courses 或 [course, personal]）"
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return None, "缺少必要参数：sources（personal | course | all | enrolled_courses 或 [course, personal]）"
        if s in ("personal", "course", "all", "enrolled_courses"):
            return s, None
        return None, f"非法 sources: {raw!r}（仅允许 personal、course、all、enrolled_courses）"
    if isinstance(raw, list):
        items: set[str] = set()
        for x in raw:
            if not isinstance(x, str) or not x.strip():
                return None, f"非法 sources 列表元素: {x!r}"
            v = x.strip().lower()
            if v not in ("course", "personal"):
                return None, f"非法 sources 列表元素: {x!r}（仅允许 course、personal）"
            items.add(v)
        if not items:
            return None, "sources 数组不能为空"
        if items == {"course", "personal"}:
            return "all", None
        if items == {"course"}:
            return "course", None
        if items == {"personal"}:
            return "personal", None
        return None, "非法 sources 数组"
    return None, f"sources 类型非法: {type(raw).__name__}"


async def _handle_knowledge_query(args: dict) -> str:
    import asyncio

    question = args.get("question")
    if not question:
        return tool_error("缺少必要参数：question")
    mode = args.get("mode", "hybrid")
    raw_sources = args.get("sources")
    sources, src_err = _normalize_knowledge_sources(raw_sources)
    if src_err or sources is None:
        return tool_error(src_err or "非法 sources")

    try:
        ctx = get_current_runtime()
        user_id = ctx.user_id
        course_id = str(ctx.course_id).strip() if ctx.course_id else ""
    except RuntimeError as exc:
        return tool_error(f"缺少运行上下文: {exc}")

    if sources in ("course", "all") and not course_id:
        return tool_error("当前会话未绑定课程，无法使用 sources=course 或 all")
    if sources == "enrolled_courses" and course_id:
        return tool_error("sources=enrolled_courses 仅用于未绑定单课的会话（问答中心）")

    raw_top = args.get("top_k", 5)
    try:
        top_k = int(raw_top) if raw_top is not None else 5
    except (TypeError, ValueError):
        return tool_error(f"非法 top_k: {raw_top!r}（需为整数）")
    if top_k < 1 or top_k > 20:
        return tool_error("top_k 必须在 1 到 20 之间")

    results: list[dict[str, Any]] = []
    leg_errors: list[str] = []

    if sources == "enrolled_courses":
        try:
            cids = await asyncio.to_thread(_sync_list_enrolled_course_ids, user_id)
        except Exception as exc:
            logger.error("knowledge_query enrolled_courses list failed: %s", exc)
            return tool_error(f"无法获取可检索课程列表: {exc}")
        if not cids:
            leg_errors.append("enrolled_courses:no_courses")
        merged_raw: list[tuple[str, dict[str, Any]]] = []
        max_courses = 40
        for cid in cids[:max_courses]:
            try:
                ch = await asyncio.to_thread(
                    _sync_verify_and_query_course,
                    user_id,
                    cid,
                    str(question),
                    top_k,
                    str(mode),
                )
            except PermissionError:
                continue
            except Exception as exc:
                logger.warning("knowledge_query course %s leg failed: %s", cid, exc)
                leg_errors.append(f"course:{cid}:{exc}")
                continue
            for h in ch:
                merged_raw.append((cid, h))
        merged_raw.sort(
            key=lambda t: float((t[1].get("relevance_score") or 0.0)),
            reverse=True,
        )
        merged_raw = merged_raw[:top_k]
        mids: set[str] = set()
        for _cid, h in merged_raw:
            meta = h.get("metadata") or {}
            mid = meta.get("material_id")
            if isinstance(mid, str) and mid:
                mids.add(mid)
        titles = _fetch_material_titles(mids)
        for cid_row, h in merged_raw:
            meta = h.get("metadata") or {}
            mid = meta.get("material_id")
            mid_s = str(mid) if mid else None
            results.append(
                {
                    "origin": "course",
                    "chunk_id": h.get("chunk_id", ""),
                    "text": str(h.get("text", ""))[:8000],
                    "course_id": cid_row,
                    "material_id": mid_s,
                    "material_title": titles.get(mid_s) if mid_s else None,
                    "relevance_score": float(h.get("relevance_score", 0.0)),
                },
            )

    if sources in ("course", "all"):
        try:
            course_hits = await asyncio.to_thread(
                _sync_verify_and_query_course,
                user_id,
                course_id,
                str(question),
                top_k,
                str(mode),
            )
        except PermissionError as exc:
            return tool_error(str(exc))
        except Exception as exc:
            logger.error("knowledge_query course leg failed: %s", exc)
            leg_errors.append(f"course:{exc}")
            course_hits = []
        else:
            mids: set[str] = set()
            for h in course_hits:
                meta = h.get("metadata") or {}
                mid = meta.get("material_id")
                if isinstance(mid, str) and mid:
                    mids.add(mid)
            titles = _fetch_material_titles(mids)
            for h in course_hits:
                meta = h.get("metadata") or {}
                mid = meta.get("material_id")
                mid_s = str(mid) if mid else None
                results.append(
                    {
                        "origin": "course",
                        "chunk_id": h.get("chunk_id", ""),
                        "text": str(h.get("text", ""))[:8000],
                        "course_id": course_id,
                        "material_id": mid_s,
                        "material_title": titles.get(mid_s) if mid_s else None,
                        "relevance_score": float(h.get("relevance_score", 0.0)),
                    },
                )

    if sources in ("personal", "all"):
        try:
            from rag_mvp.engine import personal_retrieval_hits_sync

            personal_hits = await asyncio.to_thread(
                personal_retrieval_hits_sync,
                str(question),
                mode=str(mode),
                top_k=top_k,
            )
        except Exception as exc:
            logger.error("knowledge_query personal leg failed: %s", exc)
            leg_errors.append(f"personal:{exc}")
        else:
            for h in personal_hits:
                results.append(
                    {
                        "origin": "personal",
                        "chunk_id": h.get("chunk_id", ""),
                        "text": str(h.get("text", ""))[:8000],
                        "course_id": None,
                        "material_id": None,
                        "material_title": None,
                        "relevance_score": float(h.get("relevance_score", 0.0)),
                    },
                )

    if leg_errors and not results:
        return tool_error("; ".join(leg_errors))

    if not results:
        return tool_result("知识库中暂无相关信息。")

    display = next(
        (str(r.get("text", "")) for r in results if str(r.get("text", "")).strip()),
        json.dumps(results, ensure_ascii=False, indent=2),
    )
    extra: dict[str, Any] = {"payload": results}
    if leg_errors:
        extra["retrieval_warnings"] = leg_errors
    return tool_result(display[:12000], **extra)


async def _handle_generate_quiz(args: dict) -> str:
    count = max(1, min(int(args.get("count", 5)), 20))
    question_type = args.get("question_type", "mixed")
    try:
        from rag_mvp.question_gen import (  # lazy import
            DEFAULT_OBJECTIVE_WEIGHTS,
            generate,
        )

        type_weights: dict[str, float] | None
        if question_type == "mixed":
            type_weights = None
        else:
            type_weights = {question_type: 1.0}

        result: dict[str, Any] = generate(
            count=count,
            type_weights=type_weights,
            objective_weights=DEFAULT_OBJECTIVE_WEIGHTS,
        )

        questions: list[dict] = result.get("questions", [])
        total: int = result.get("total", len(questions))

        lines: list[str] = [f"**已生成 {total} 道练习题**\n"]
        for idx, q in enumerate(questions, 1):
            q_type = q.get("type", "")
            stem = q.get("question", "")
            options: list[str] = q.get("options", [])
            answer = q.get("answer", "")
            explanation = q.get("explanation", "")

            lines.append(f"**{idx}. 【{q_type}】** {stem}")
            for opt in options:
                lines.append(f"   {opt}")
            if answer:
                lines.append(f"   > 答案：{answer}")
            if explanation:
                lines.append(f"   > 解析：{explanation}")
            lines.append("")

        summary = "\n".join(lines)
        return tool_result(summary, payload=result)
    except Exception as exc:
        logger.error("generate_quiz failed: %s", exc)
        return tool_error(str(exc))


async def _handle_ingest_document(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return tool_error("缺少必要参数：path")
    try:
        from pathlib import Path as _Path

        from rag_mvp.engine import ingest_file, ingest_folder  # lazy import

        target = _Path(path)
        if target.is_dir():
            ingest_folder(target)
            summary = f"目录 '{path}' 下的文档已全部导入知识库。"
        else:
            ingest_file(target)
            summary = f"文档 '{path}' 已导入知识库。"
        return tool_result(summary)
    except Exception as exc:
        logger.error("ingest_document failed: %s", exc)
        return tool_error(str(exc))


async def _handle_build_mindmap(args: dict) -> str:
    source = args.get("source", "")
    if not source:
        return tool_error("缺少必要参数：source")
    refine = bool(args.get("refine", False))
    try:
        from rag_mvp.mindmap import build_structure_mindmap  # lazy import

        out_paths = build_structure_mindmap(source, refine=refine)
        if not out_paths:
            return tool_result("思维导图生成完毕，但未找到可输出的文件路径。")
        path_list = "\n".join(str(p) for p in out_paths)
        return tool_result(
            f"思维导图已生成，共 {len(out_paths)} 个文件：\n{path_list}",
            payload=[str(p) for p in out_paths],
        )
    except Exception as exc:
        logger.error("build_mindmap failed: %s", exc)
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_KNOWLEDGE_QUERY["name"],
        description=_SCHEMA_KNOWLEDGE_QUERY["description"],
        input_schema=_SCHEMA_KNOWLEDGE_QUERY["parameters"],
        handler=_handle_knowledge_query,
        toolset="rag",
        permissions=[ToolPermission.READ],
        emoji="🔍",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_GENERATE_QUIZ["name"],
        description=_SCHEMA_GENERATE_QUIZ["description"],
        input_schema=_SCHEMA_GENERATE_QUIZ["parameters"],
        handler=_handle_generate_quiz,
        toolset="rag",
        permissions=[ToolPermission.READ],
        emoji="📝",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_INGEST_DOCUMENT["name"],
        description=_SCHEMA_INGEST_DOCUMENT["description"],
        input_schema=_SCHEMA_INGEST_DOCUMENT["parameters"],
        handler=_handle_ingest_document,
        toolset="rag",
        permissions=[ToolPermission.READ, ToolPermission.EXTERNAL],
        emoji="📥",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_BUILD_MINDMAP["name"],
        description=_SCHEMA_BUILD_MINDMAP["description"],
        input_schema=_SCHEMA_BUILD_MINDMAP["parameters"],
        handler=_handle_build_mindmap,
        toolset="rag",
        permissions=[ToolPermission.READ, ToolPermission.EXTERNAL],
        emoji="🗺️",
    )
)
