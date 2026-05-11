"""Agent-driven assignment generation pipeline.

Pipeline:
    1. PlannerAgent._run_planner(teacher_request) → Blueprint dict
    2. _retrieve_candidates(course_id, topic_hint, count) → entity candidates with contexts
    3. assign_objective_format_pairs() → [(objective, format), ...]
    4. generate_one() per question pair (from question_gen, with semaphore)
    5. ReviewerAgent._run_reviewer(questions, blueprint) → QualityReport dict
    6. DB updated at each stage via psycopg3 sync connection

Course RAG is PostgreSQL-backed (PGKVStorage / PGVectorStorage / PGGraphStorage).
Entity retrieval uses existing course_aquery_data() — no direct table queries needed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from typing import Any

from loguru import logger

from .config import settings
from .engine import course_aquery_data
from .llm import llm_model_func
from .worker_async_loop import is_worker_async_loop_started, run_worker_coroutine
from .question_gen import (
    DEFAULT_OBJECTIVE_WEIGHTS,
    DEFAULT_TYPE_WEIGHTS,
    OBJECTIVE_FORMAT_COMPATIBILITY,
    assign_objective_format_pairs,
    generate_one,
)

# ---------------------------------------------------------------------------
# Planner Agent
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """\
你是一位专业的教学设计专家。根据教师的作业需求描述，生成一个结构化的作业命题蓝图。

规则：
1. 根据需求描述判断合适的题目数量（默认 10，最多 20）
2. 根据教学目标自动调整题型和认知层次比例，各权重之和必须等于 1.0
3. 输出必须是严格的 JSON，不含任何其他文字或 markdown 代码块
4. topic_hint 应为课程核心主题关键词，用于 RAG 检索，应简洁（5-20字）
5. difficulty 只能为 easy / medium / hard 之一
"""

_PLANNER_PROMPT_TMPL = """\
教师需求描述：
{teacher_request}

请生成作业命题蓝图，严格按以下 JSON 格式输出（不要添加任何注释）：
{{
  "title": "作业标题",
  "topic_hint": "RAG检索用的核心主题关键词",
  "difficulty": "medium",
  "count": 10,
  "type_weights": {{
    "single_choice": 0.4,
    "multi_choice": 0.1,
    "fill_blank": 0.3,
    "short_answer": 0.2
  }},
  "objective_weights": {{
    "knowledge": 0.3,
    "comprehension": 0.2,
    "application": 0.3,
    "synthesis": 0.1,
    "innovation": 0.1
  }},
  "estimated_minutes": 30
}}
"""


async def _run_planner(teacher_request: str) -> dict[str, Any]:
    prompt = _PLANNER_PROMPT_TMPL.format(teacher_request=teacher_request.strip())
    raw = await llm_model_func(prompt, system_prompt=_PLANNER_SYSTEM)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"PlannerAgent returned no JSON: {raw[:300]}")
    blueprint = json.loads(m.group())
    # Clamp count
    blueprint["count"] = max(1, min(20, int(blueprint.get("count", 10))))
    return blueprint


# ---------------------------------------------------------------------------
# Entity / context retrieval from course RAG (PostgreSQL backend)
# ---------------------------------------------------------------------------


async def _retrieve_candidates(
    course_id: str,
    topic_hint: str,
    count: int,
) -> list[dict[str, Any]]:
    """Query course RAG (hybrid mode) to build entity candidates with text contexts.

    Returns a list of dicts: {name, score, context, chunk_ids}.
    Falls back to chunk-based pseudo-entities if no named entities are returned.
    """
    query = (
        f"{topic_hint}的核心概念、知识点和重要原理" if topic_hint
        else "课程核心概念、知识点和重要原理"
    )
    top_k = min(count * 3, 60)

    raw = await course_aquery_data(course_id, query, mode="hybrid", top_k=top_k)
    data = raw.get("data") or {}

    entities: list[dict] = list(data.get("entities") or [])
    chunks: list[dict] = list(data.get("chunks") or [])

    # chunk_id → content lookup
    chunk_map: dict[str, str] = {}
    for ch in chunks:
        cid = str(ch.get("id") or ch.get("chunk_id") or "").strip()
        content = str(ch.get("content") or "").strip()
        if cid and content:
            chunk_map[cid] = content

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i, entity in enumerate(entities):
        name = str(entity.get("entity_name") or entity.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        # Find chunks that mention this entity
        entity_chunk_ids = [
            cid for cid, content in chunk_map.items()
            if name.lower() in content.lower()
        ][:3]

        if entity_chunk_ids:
            context = "\n\n".join(chunk_map[cid] for cid in entity_chunk_ids)[:2500]
        elif chunk_map:
            # No direct mention: fall back to top-k retrieved chunks as context
            entity_chunk_ids = list(chunk_map.keys())[:2]
            context = "\n\n".join(chunk_map[cid] for cid in entity_chunk_ids)[:2500]
        else:
            continue

        score = float(
            entity.get("importance_score")
            or entity.get("rank_score")
            or (1.0 / (i + 1))
        )
        candidates.append(
            {"name": name, "score": score, "context": context, "chunk_ids": entity_chunk_ids}
        )

    # If RAG returned no usable entities, create pseudo-entities from raw chunks
    if not candidates and chunk_map:
        logger.warning(
            "course_aquery_data returned no named entities for course={}, "
            "falling back to chunk-based candidates",
            course_id,
        )
        for i, (cid, content) in enumerate(list(chunk_map.items())[:count]):
            candidates.append(
                {
                    "name": f"知识点{i + 1}",
                    "score": 1.0 / (i + 1),
                    "context": content[:2500],
                    "chunk_ids": [cid],
                }
            )

    return candidates[: count * 2]


# ---------------------------------------------------------------------------
# Reviewer Agent
# ---------------------------------------------------------------------------

_REVIEWER_SYSTEM = """\
你是一位专业的考试质量评审专家。对题目进行质量评审，检查清晰度、准确性和难度匹配性。

规则：
1. 严格按 JSON 格式输出，不含其他文字或 markdown 代码块
2. overall_score 为 0-1 浮点数，0.7 及以上视为通过
3. 每道题的 clarity、difficulty_match 也是 0-1 浮点数
4. issues 为发现的具体问题列表（字符串数组），无问题时为空数组 []
5. suggestion 为改进建议字符串，无建议时为 null
"""

_REVIEWER_PROMPT_TMPL = """\
作业蓝图：
  标题：{title}
  难度：{difficulty}
  认知目标分布：{objective_weights}

题目列表（共 {total} 道）：
{questions_text}

请对以上题目进行质量评审，严格按以下 JSON 格式输出：
{{
  "overall_score": 0.85,
  "passed": true,
  "threshold": 0.7,
  "question_reviews": [
    {{
      "id": 1,
      "clarity": 0.9,
      "difficulty_match": 0.8,
      "issues": [],
      "suggestion": null
    }}
  ],
  "failed_ids": [],
  "summary": "总体评价文字"
}}
"""


async def _run_reviewer(questions: list[dict], blueprint: dict) -> dict[str, Any]:
    parts: list[str] = []
    for q in questions:
        line = f"[{q['id']}] ({q['type']}/{q['objective']}) {q['question']}"
        if q.get("options"):
            line += f"\n  选项: {'; '.join(q['options'])}"
        line += f"\n  答案: {q['answer']}"
        parts.append(line)
    questions_text = "\n\n".join(parts)

    prompt = _REVIEWER_PROMPT_TMPL.format(
        title=blueprint.get("title", ""),
        difficulty=blueprint.get("difficulty", "medium"),
        objective_weights=json.dumps(
            blueprint.get("objective_weights", {}), ensure_ascii=False
        ),
        total=len(questions),
        questions_text=questions_text[:4000],
    )

    raw = await llm_model_func(prompt, system_prompt=_REVIEWER_SYSTEM)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"ReviewerAgent returned no JSON: {raw[:300]}")
    return json.loads(m.group())


# ---------------------------------------------------------------------------
# DB helpers (psycopg3 sync)
# ---------------------------------------------------------------------------


def _db_update(conn: Any, assignment_id: str, **fields: Any) -> None:
    """Update assignments table row using a psycopg3 sync connection."""
    if not fields:
        return
    set_clauses: list[str] = []
    values: list[Any] = []
    for col, val in fields.items():
        set_clauses.append(f"{col} = %s")
        values.append(
            json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else val
        )
    values.append(assignment_id)
    sql = (
        f"UPDATE assignments SET {', '.join(set_clauses)}, updated_at = NOW() "
        f"WHERE id = %s::uuid"
    )
    conn.execute(sql, values)
    conn.commit()


# ---------------------------------------------------------------------------
# Single-question regeneration  (called from FastAPI route — async)
# ---------------------------------------------------------------------------


async def regenerate_one_question(
    course_id: str,
    entity_name: str,
    q_type: str,
    objective: str,
    q_id: int,
    extra_requirements: str = "",
) -> dict[str, Any] | None:
    """Regenerate a single question using course RAG context.

    Returns a question dict (same schema as generate_one output) or None on failure.
    Called from the edu-agent FastAPI server (async context).
    """
    query = f"{entity_name} {extra_requirements}".strip()
    raw = await course_aquery_data(course_id, query, mode="local", top_k=10)
    data = raw.get("data") or {}

    chunks: list[dict] = list(data.get("chunks") or [])
    chunk_map: dict[str, str] = {
        str(ch.get("id", "")): str(ch.get("content", ""))
        for ch in chunks
        if ch.get("id") and ch.get("content")
    }

    entity_chunk_ids = [
        cid for cid, content in chunk_map.items()
        if entity_name.lower() in content.lower()
    ][:3] or list(chunk_map.keys())[:3]

    context = "\n\n".join(chunk_map[cid] for cid in entity_chunk_ids if cid in chunk_map)[:2500]

    if not context:
        logger.warning(
            "regenerate_one_question: no context found for entity={} course={}",
            entity_name, course_id,
        )
        return None

    if extra_requirements:
        context = f"{context}\n\n【教师额外要求】\n{extra_requirements}"

    return await generate_one(
        entity_name=entity_name,
        context=context,
        q_type=q_type,
        q_id=q_id,
        score=1.0,
        chunk_ids=entity_chunk_ids,
        objective=objective,
    )


# ---------------------------------------------------------------------------
# Main orchestrator (called synchronously by the Redis Stream worker)
# ---------------------------------------------------------------------------


def generate_assignment(
    assignment_id: str,
    course_id: str,
    teacher_request: str,
    conn: Any,
    structured_params: dict | None = None,
) -> None:
    """Full pipeline: plan → retrieve → generate → review → update DB.

    Runs the async pipeline in a dedicated event loop so it can be called
    from the synchronous Redis Stream worker.
    conn is a psycopg3 sync connection (autocommit=False).
    When structured_params is provided, the Planner LLM step is skipped and
    a Blueprint is constructed directly from the params.
    """
    use_worker_loop = is_worker_async_loop_started()

    async def _pipeline() -> None:
        pipe_conn: Any = None
        if use_worker_loop:
            from .db import connect_sync

            pipe_conn = connect_sync(autocommit=False)
        else:
            pipe_conn = conn
        try:
            try:
                logger.info(
                    "Assignment generation started assignment_id={} course={}",
                    assignment_id, course_id,
                )

                # ── Step 1: Planner (or structured bypass) ───────────────────────
                if structured_params is not None:
                    # Build Blueprint directly from structured params — skip LLM Planner
                    sp = structured_params
                    difficulty = sp.get("difficulty", "medium")
                    count = max(1, min(50, int(sp.get("count", 10))))
                    type_weights_raw: dict = sp.get("typeWeights") or DEFAULT_TYPE_WEIGHTS
                    obj_weights_raw: dict = sp.get("objectiveWeights") or DEFAULT_OBJECTIVE_WEIGHTS
                    # Normalise weights to sum to 1
                    tw_total = sum(type_weights_raw.values()) or 1
                    ow_total = sum(obj_weights_raw.values()) or 1
                    tw = {k: v / tw_total for k, v in type_weights_raw.items()}
                    ow = {k: v / ow_total for k, v in obj_weights_raw.items()}

                    lesson_names: list[str] = sp.get("lessonNames") or []
                    kp: list[str] = sp.get("knowledgePoints") or []
                    topic_parts = lesson_names + kp
                    topic_hint = "、".join(topic_parts[:4]) if topic_parts else ""

                    blueprint: dict[str, Any] = {
                        "title": teacher_request.strip() or "结构化作业",
                        "topic_hint": topic_hint,
                        "difficulty": difficulty,
                        "count": count,
                        "type_weights": tw,
                        "objective_weights": ow,
                        "estimated_minutes": count * 2,
                    }
                    logger.info(
                        "Blueprint built from structured_params (bypass planner): title={}",
                        blueprint["title"],
                    )
                else:
                    blueprint = await _run_planner(teacher_request)
                    logger.info("Blueprint ready: title={}", blueprint.get("title"))
                _db_update(pipe_conn, assignment_id, blueprint=blueprint)

                # ── Step 2: Retrieve entity candidates from course RAG ─────────
                count = int(blueprint.get("count", 10))
                topic_hint = str(blueprint.get("topic_hint", ""))
                candidates = await _retrieve_candidates(course_id, topic_hint, count)

                if not candidates:
                    raise RuntimeError(
                        "No entities or chunks found in course RAG. "
                        "Ensure course materials are indexed (status=READY)."
                    )

                # ── Step 3: Generate questions ─────────────────────────────────
                type_weights: dict = blueprint.get("type_weights") or DEFAULT_TYPE_WEIGHTS
                obj_weights: dict = blueprint.get("objective_weights") or DEFAULT_OBJECTIVE_WEIGHTS

                pairs = assign_objective_format_pairs(
                    count, obj_weights, type_weights, OBJECTIVE_FORMAT_COMPATIBILITY
                )

                sem = asyncio.Semaphore(settings.llm_max_async)

                async def _guarded(idx: int, entity: dict, obj: str, fmt: str) -> dict | None:
                    async with sem:
                        return await generate_one(
                            entity_name=entity["name"],
                            context=entity["context"],
                            q_type=fmt,
                            q_id=idx + 1,
                            score=entity["score"],
                            chunk_ids=entity["chunk_ids"],
                            objective=obj,
                        )

                tasks = [
                    _guarded(idx, candidates[idx % len(candidates)], obj, fmt)
                    for idx, (obj, fmt) in enumerate(pairs)
                    if candidates[idx % len(candidates)]["context"]
                ]

                raw_results = await asyncio.gather(*tasks, return_exceptions=True)
                questions: list[dict] = []
                for r in raw_results:
                    if isinstance(r, dict):
                        r.setdefault("score", 5)  # default point value per question
                        questions.append(r)
                    elif isinstance(r, Exception):
                        logger.warning("Question generation task failed: {}", r)

                if not questions:
                    raise RuntimeError("All question generation tasks failed — check LLM logs.")

                # Re-number sequentially after filtering
                for seq, q in enumerate(questions, 1):
                    q["id"] = seq

                _db_update(pipe_conn, assignment_id, questions=questions)
                logger.info(
                    "{} questions generated for assignment {}",
                    len(questions), assignment_id,
                )

                # ── Step 4: Review ─────────────────────────────────────────────
                quality_report = await _run_reviewer(questions, blueprint)
                _db_update(
                    pipe_conn,
                    assignment_id,
                    quality_report=quality_report,
                    status="DRAFT",
                )
                logger.info(
                    "Assignment {} DRAFT — quality_score={}",
                    assignment_id, quality_report.get("overall_score"),
                )

            except Exception as exc:
                logger.exception(
                    "Assignment generation failed assignment_id={}", assignment_id
                )
                try:
                    _db_update(
                        pipe_conn,
                        assignment_id,
                        status="FAILED",
                        error_message=str(exc)[:500],
                    )
                except Exception:
                    logger.warning("Could not write FAILED status to DB")
                raise
        finally:
            if use_worker_loop and pipe_conn is not None:
                try:
                    pipe_conn.close()
                except Exception:
                    pass

    if use_worker_loop:
        run_worker_coroutine(_pipeline(), timeout=None)
        return

    # Run async pipeline — avoid nesting if a loop is already running (e.g. tests)
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(_pipeline())).result()
    except RuntimeError:
        asyncio.run(_pipeline())
