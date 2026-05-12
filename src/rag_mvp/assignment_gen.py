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

import psycopg
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
    _is_meta_question,
)

# ---------------------------------------------------------------------------
# Entity filters (Tier-0 only; semantic filtering via _gate_entities_with_llm)
# ---------------------------------------------------------------------------


def _is_valid_entity(name: str) -> bool:
    """Basic sanity check: length in [2, 30] and not all punctuation/digits."""
    name = name.strip()
    if not (2 <= len(name) <= 30):
        return False
    if re.fullmatch(r"[\d\s\W]+", name):
        return False
    return True


def _load_course_image_map(conn: Any, course_id: str) -> dict[str, list[dict]]:
    """Load material_images records for a course, keyed by material_id.

    Returns: {material_id: [{page_idx, minio_url}, ...]}
    """
    result: dict[str, list[dict]] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mi.material_id::text, mi.page_idx, mi.minio_url
                FROM material_images mi
                JOIN materials m ON m.id = mi.material_id
                WHERE m.course_id = %s::uuid AND m.is_deleted = false
                ORDER BY mi.material_id, mi.page_idx
                """,
                (course_id,),
            )
            for row in cur.fetchall():
                mid, page, url = row[0], row[1], row[2]
                result.setdefault(mid, []).append({"page_idx": page, "minio_url": url})
    except Exception as exc:
        logger.warning("Could not load course image map (course={}): {}", course_id, exc)
    return result

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
        if not _is_valid_entity(name):
            logger.debug("Skipping invalid entity: '{}'", name)
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


_ENTITY_GATE_SYSTEM = """\
你是课程考试命题前的「实体候选」审核员。下面每个「实体」来自知识图谱/RAG，并附带一段上下文摘要。

任务：判断该实体是否适合作为**学科/技术知识点**用于出题（与作业蓝图的 topic_hint、难度、题量意图一致）。

keep=false 的典型情况（示例，不限于）：
- 文档元信息：目录、大纲、章节列表、学习目标列表、考核方式、教学计划等教务表述
- 纯结构标签、无实质考查价值的名称

keep=true：概念、方法、协议、算法、定理、技能点等可独立考查的内容。

输出要求：
1. 只输出严格 JSON，不要 markdown 代码块或其它文字
2. JSON 格式：{"decisions":[{"name":"实体名","keep":true或false,"reason":"一句中文理由"}]}
3. 对输入列表中的**每一个**实体必须给出一条 decision，name 必须与输入完全一致
"""


async def _gate_entities_with_llm(
    blueprint: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter entity candidates using a single- or multi-batch LLM JSON gate.

    If the LLM call fails or returns unusable JSON, returns *candidates* unchanged
    (sorted by score descending for stable generation order).
    """

    def _coerce_keep(val: Any) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "是")
        return bool(val)

    if not candidates:
        return candidates

    sorted_c = sorted(
        candidates,
        key=lambda c: -float(c.get("score") or 0.0),
    )
    count = max(1, int(blueprint.get("count", 10)))
    # Judge the highest-ranked slice; cap token budget
    m_cap = min(len(sorted_c), max(8, min(count * 2, 40)))
    head = sorted_c[:m_cap]
    tail = sorted_c[m_cap:]

    def _excerpt(ctx: str, limit: int = 360) -> str:
        s = (ctx or "").strip().replace("\n", " ")
        return s[:limit] + ("…" if len(s) > limit else "")

    batch_size = 16
    all_decisions: list[dict[str, Any]] = []

    for batch_start in range(0, len(head), batch_size):
        batch = head[batch_start : batch_start + batch_size]
        lines = []
        for i, c in enumerate(batch, 1):
            lines.append(
                f"{i}. 实体名：{c['name']}\n   上下文摘要：{_excerpt(c.get('context', ''))}"
            )
        user_msg = (
            f"作业蓝图摘要：\n"
            f"- 标题：{blueprint.get('title', '')}\n"
            f"- topic_hint：{blueprint.get('topic_hint', '')}\n"
            f"- 难度：{blueprint.get('difficulty', 'medium')}\n"
            f"- 题目数量：{count}\n\n"
            f"请对以下 {len(batch)} 个实体逐一给出 keep 判断：\n\n"
            + "\n\n".join(lines)
        )
        try:
            raw = await llm_model_func(
                user_msg,
                system_prompt=_ENTITY_GATE_SYSTEM,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning("Entity gate LLM failed: {}; using ungated candidates", exc)
            return sorted_c

        raw = re.sub(r"```(?:json)?", "", raw).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            logger.warning("Entity gate: no JSON in response; using ungated candidates")
            return sorted_c
        try:
            payload = json.loads(m.group())
        except json.JSONDecodeError as exc:
            logger.warning("Entity gate JSON parse error: {}; using ungated candidates", exc)
            return sorted_c

        decs = payload.get("decisions")
        if not isinstance(decs, list):
            logger.warning("Entity gate: missing decisions[]; using ungated candidates")
            return sorted_c
        all_decisions.extend([d for d in decs if isinstance(d, dict)])

    expected = {c["name"] for c in head}
    kept_map: dict[str, bool] = {name: True for name in expected}
    for d in all_decisions:
        name = str(d.get("name", "")).strip()
        if name in expected:
            kept_map[name] = _coerce_keep(d.get("keep"))

    head_filtered = [c for c in head if kept_map.get(c["name"], True)]

    if not head_filtered:
        logger.warning(
            "Entity gate: zero kept from {} head candidates; falling back to full head",
            len(head),
        )
        head_filtered = head[: max(1, min(len(head), count))]

    dropped = [c["name"] for c in head if not kept_map.get(c["name"], True)]
    if dropped:
        logger.info("Entity gate dropped {} names: {}", len(dropped), dropped[:20])

    seen_out: set[str] = {c["name"] for c in head_filtered}
    out: list[dict[str, Any]] = list(head_filtered)
    # Ensure enough pool for modulo indexing: extend with unjudged tail if short
    min_pool = max(count * 2, 10)
    for c in tail:
        if len(out) >= min_pool:
            break
        if c["name"] not in seen_out:
            out.append(c)
            seen_out.add(c["name"])
    # Any remaining head not in out (shouldn't happen) — skip
    return out


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
# Fixer Agent
# ---------------------------------------------------------------------------

_FIXER_SYSTEM = """\
你是一位专业的考试题目修复专家。对评审未通过的题目进行精准改写。

规则：
1. 只允许改写 failed_ids 中指定的题目，其他题目原样保留不得修改
2. 改写必须在原题的核心知识点（entity）范围内，不得引入新知识点或新实体
3. 严禁在改写后的题目中引用图表（如图所示/见下表/附图）、大纲、★符号、目录等
4. 若某题属于元问题（引用文档结构/图表）或无法在原知识点范围内修复，则将该题标记为 "DELETE"
5. 输出必须是严格的 JSON 数组，不含其他文字或 markdown 代码块
"""

_FIXER_PROMPT_TMPL = """\
以下是评审未通过的题目及对应问题，请逐题修复或标记删除：

{failed_items}

请严格按以下 JSON 数组格式输出（每个元素对应一道题）：
[
  {{
    "id": 1,
    "action": "rewrite",
    "question": "修改后的题目",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "answer": "A",
    "explanation": "修改后的解析"
  }},
  {{
    "id": 2,
    "action": "delete"
  }}
]
"""


async def _run_fixer(
    questions: list[dict],
    blueprint: dict,
    quality_report: dict,
    candidates: list[dict],
) -> list[dict]:
    """Rewrite or delete failed questions, then replenish deleted ones from remaining candidates."""
    failed_ids: set[int] = set(quality_report.get("failed_ids") or [])
    reviews_by_id: dict[int, dict] = {
        r["id"]: r for r in (quality_report.get("question_reviews") or [])
    }

    if not failed_ids:
        return questions

    # Build failed items text for LLM
    failed_items_parts: list[str] = []
    for q in questions:
        if q["id"] not in failed_ids:
            continue
        review = reviews_by_id.get(q["id"], {})
        issues_text = "; ".join(review.get("issues") or []) or "无具体问题"
        suggestion = review.get("suggestion") or "无"
        item = (
            f"题目ID={q['id']} (entity: {q.get('entity', '')}, type: {q['type']}/{q['objective']})\n"
            f"题目: {q['question']}\n"
            f"答案: {q['answer']}\n"
            f"评审问题: {issues_text}\n"
            f"改进建议: {suggestion}"
        )
        failed_items_parts.append(item)

    prompt = _FIXER_PROMPT_TMPL.format(failed_items="\n\n".join(failed_items_parts))
    try:
        raw = await llm_model_func(prompt, system_prompt=_FIXER_SYSTEM)
    except Exception as exc:
        logger.warning("FixerAgent LLM call failed: {}", exc)
        return questions

    raw = re.sub(r"```(?:json)?", "", raw).strip()
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not arr_match:
        logger.warning("FixerAgent returned no JSON array; skipping fix round")
        return questions

    try:
        fix_items: list[dict] = json.loads(arr_match.group())
    except json.JSONDecodeError as exc:
        logger.warning("FixerAgent JSON parse error: {}", exc)
        return questions

    # Build used entity set for replenishment
    used_entities: set[str] = {q.get("entity", "") for q in questions}
    delete_ids: set[int] = set()

    # Apply rewrites
    q_by_id: dict[int, dict] = {q["id"]: q for q in questions}
    for fix in fix_items:
        qid = fix.get("id")
        if qid not in q_by_id:
            continue
        if fix.get("action") == "delete" or _is_meta_question(fix.get("question", "")):
            delete_ids.add(qid)
            logger.info("FixerAgent: deleting Q{} (meta/unfixable)", qid)
        elif fix.get("action") == "rewrite" and fix.get("question"):
            orig = q_by_id[qid]
            orig["question"] = fix["question"]
            orig["options"] = fix.get("options", orig.get("options", []))
            orig["answer"] = fix.get("answer", orig["answer"])
            orig["explanation"] = fix.get("explanation", orig.get("explanation", ""))
            logger.info("FixerAgent: rewrote Q{} (entity={})", qid, orig.get("entity"))

    # Remove deleted questions
    questions = [q for q in questions if q["id"] not in delete_ids]

    # Replenish deleted slots using unused candidates
    deleted_count = len(delete_ids)
    if deleted_count > 0:
        spare_candidates = [c for c in candidates if c["name"] not in used_entities]
        next_id = max((q["id"] for q in questions), default=0) + 1
        for i, cand in enumerate(spare_candidates[:deleted_count]):
            new_q = await generate_one(
                entity_name=cand["name"],
                context=cand["context"],
                q_type="short_answer",
                q_id=next_id + i,
                score=cand["score"],
                chunk_ids=cand["chunk_ids"],
                objective="knowledge",
            )
            if new_q:
                questions.append(new_q)
                logger.info("FixerAgent: replenished Q{} with entity='{}'", next_id + i, cand["name"])

    return questions


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

                logger.info(
                    "Blueprint JSON:\n{}",
                    json.dumps(blueprint, ensure_ascii=False, indent=2),
                )
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

                candidates = await _gate_entities_with_llm(blueprint, candidates)
                if not candidates:
                    raise RuntimeError(
                        "Entity gate removed all candidates; broaden topic_hint or check RAG."
                    )

                # Load course image map for source_images traceability
                course_image_map = _load_course_image_map(pipe_conn, course_id)
                # Flatten to list sorted by page_idx for fast lookup
                _all_images: list[dict] = sorted(
                    [img for imgs in course_image_map.values() for img in imgs],
                    key=lambda x: x["page_idx"],
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

                # Attach source_images: for each question, find images on adjacent pages
                # (pages within ±1 of any chunk's page reference — best-effort heuristic)
                if _all_images:
                    for q in questions:
                        q.setdefault("source_images", [])

                _db_update(pipe_conn, assignment_id, questions=questions)
                logger.info(
                    "{} questions generated for assignment {}",
                    len(questions), assignment_id,
                )

                # ── Step 4: Review → Fix loop (max 3 rounds, PASS ≥ 0.85) ──────
                MAX_ROUNDS = 3
                PASS_SCORE = 0.85
                quality_report: dict[str, Any] = {}
                for _round in range(1, MAX_ROUNDS + 1):
                    quality_report = await _run_reviewer(questions, blueprint)
                    score = float(quality_report.get("overall_score", 0))
                    passed = quality_report.get("passed", False) or score >= PASS_SCORE
                    logger.info(
                        "Review round {}/{} — assignment={} score={:.3f} passed={}",
                        _round, MAX_ROUNDS, assignment_id, score, passed,
                    )
                    if passed:
                        break
                    if _round < MAX_ROUNDS:
                        logger.info(
                            "Score {:.3f} < {:.2f}, running Fixer (round {}/{})",
                            score, PASS_SCORE, _round, MAX_ROUNDS,
                        )
                        questions = await _run_fixer(questions, blueprint, quality_report, candidates)
                        # Re-number after fix
                        for seq, q in enumerate(questions, 1):
                            q["id"] = seq
                    else:
                        logger.warning(
                            "Max review rounds reached for assignment={}, final score={:.3f}",
                            assignment_id, score,
                        )

                _db_update(
                    pipe_conn,
                    assignment_id,
                    questions=questions,
                    quality_report=quality_report,
                    status="DRAFT",
                )
                final_score = quality_report.get("overall_score", "N/A")
                logger.info(
                    "Assignment {} DRAFT — final_score={} total_questions={}",
                    assignment_id, final_score, len(questions),
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
