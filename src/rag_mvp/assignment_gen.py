"""Agent-driven assignment generation pipeline.

Pipeline:
    1. Extract params (count, type/obj/difficulty weights, topic_hint) from structured_params or NLP defaults
    2. _retrieve_candidates(course_id, topic_hint, count) → entity candidates with contexts
    3. assign_objective_format_pairs() + _distribute_difficulty() + _make_slots() → pre-computed slots
    4. _run_planner(teacher_request, candidates, slots, dw) → Blueprint dict (entity + focus per slot)
    5. generate_one() per blueprint question via _match_entity() lookup
    6. ReviewerAgent._run_reviewer(questions, blueprint) → QualityReport dict
    7. DB updated at each stage via psycopg3 sync connection

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
from .llm import llm_model_func, llm_chat_model_func
from .worker_async_loop import is_worker_async_loop_started, run_worker_coroutine
from .question_gen import (
    DEFAULT_OBJECTIVE_WEIGHTS,
    DEFAULT_TYPE_WEIGHTS,
    OBJECTIVE_FORMAT_COMPATIBILITY,
    assign_objective_format_pairs,
    generate_one,
    _is_meta_question,
    _call_question_llm,
)

# ---------------------------------------------------------------------------
# Entity filters (Tier-0 sanity check only; semantic filtering handled by Planner prompt)
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
你是一位专业的教学设计专家。你会收到：
1. 教师的作业需求描述
2. 预分配好的题目槽位列表（每道题的类型、认知目标、难度已确定）
3. 从课程知识图谱检索到的候选知识点实体

你的任务：
1. 为每个槽位从候选实体中选出最合适的实体（1-2个）
2. 为每道题写一句简洁的命题关注点（focus）
3. 输出作业标题

规则：
1. 输出必须是严格的 JSON，不含任何其他文字或 markdown 代码块
2. questions 数组的长度必须与输入槽位数完全一致
3. entity_names 只能选自下方候选实体列表中的名称，不得编造实体名
4. focus 应简短（10-30字），说明该题考查什么方面，供后续 LLM 生成时参考
5. 尽量让不同槽位覆盖不同知识点，避免过度重复
6. 对于 synthesis 或 application 目标的题目，可以选 1-2 个相关实体
7. 忽略名称疑似文档结构/元信息的实体（如"学习目标"、"课程大纲"等）
"""

_PLANNER_PROMPT_TMPL = """\
教师需求：{teacher_request}

预分配题目槽位（共 {count} 个，你的 questions 长度必须为 {count}）：
{slots_text}

候选知识点实体（entity_names 只能从以下名称中选择）：
{candidates_text}

请输出命题蓝图，严格按以下 JSON 格式（不要添加任何注释或 markdown）：
{{
  "title": "作业标题",
  "difficulty_weights": {difficulty_weights_json},
  "questions": [
    {{"id": 1, "entity_names": ["实体名"], "focus": "命题关注点（10-30字）"}},
    ...（共 {count} 条）
  ]
}}
"""


async def _run_planner(
    teacher_request: str,
    candidates: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    difficulty_weights: dict[str, float],
) -> dict[str, Any]:
    count = len(slots)
    slots_text = "\n".join(
        f"  id={s['id']}: type={s['type']}, objective={s['objective']}, difficulty={s['difficulty']}"
        for s in slots
    )
    candidates_text = "\n".join(
        f"  {i}. {c['name']}：{(c.get('context') or '').strip().replace(chr(10), ' ')[:80]}"
        for i, c in enumerate(candidates, 1)
    )
    dw_json = json.dumps(difficulty_weights, ensure_ascii=False)
    prompt = _PLANNER_PROMPT_TMPL.format(
        teacher_request=teacher_request.strip(),
        count=count,
        slots_text=slots_text,
        candidates_text=candidates_text,
        difficulty_weights_json=dw_json,
    )
    raw = await llm_chat_model_func(prompt, system_prompt=_PLANNER_SYSTEM)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"PlannerAgent returned no JSON: {raw[:300]}")
    blueprint = json.loads(m.group())
    blueprint.setdefault("difficulty_weights", difficulty_weights)
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


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------

def _match_entity(name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match an entity by normalised name; fallback to the highest-score candidate."""
    target = name.lower().replace(" ", "")
    for c in candidates:
        if c["name"].lower().replace(" ", "") == target:
            return c
    if candidates:
        return max(candidates, key=lambda c: float(c.get("score") or 0))
    return None


def _distribute_difficulty(count: int, difficulty_weights: dict[str, float]) -> list[str]:
    """Return a list of difficulty labels of length *count* proportional to *difficulty_weights*."""
    total = sum(difficulty_weights.values()) or 1.0
    normalized = {k: v / total for k, v in difficulty_weights.items()}
    slots: list[str] = []
    for level, weight in normalized.items():
        slots.extend([level] * round(count * weight))
    fallback = max(normalized, key=lambda k: normalized[k])
    while len(slots) < count:
        slots.append(fallback)
    return slots[:count]


def _make_slots(
    obj_fmt_pairs: list[tuple[str, str]],
    difficulty_slots: list[str],
) -> list[dict[str, Any]]:
    """Combine (objective, format) pairs with difficulty labels into slot dicts."""
    return [
        {"id": i + 1, "type": fmt, "objective": obj, "difficulty": diff}
        for i, ((obj, fmt), diff) in enumerate(zip(obj_fmt_pairs, difficulty_slots))
    ]


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
6. difficulty_match 评分基于题目的 reasoning_steps 字段与该题蓝图难度的匹配度：
   - 蓝图 easy 期望 reasoning_steps = 1，实际 1 步得满分
   - 蓝图 medium 期望 reasoning_steps = 2，实际 2 步得满分
   - 蓝图 hard 期望 reasoning_steps ≥ 3，实际 ≥ 3 步得满分
   - 偏差 1 步扣 0.3，偏差 2 步及以上扣 0.6
7. difficulty_distribution_score 评估整批题目的实际难度分布（基于 reasoning_steps）与蓝图
   difficulty_weights 的吻合度（0-1）：分布完全符合得 1.0，偏差越大分越低
"""

_REVIEWER_PROMPT_TMPL = """\
作业蓝图：
  标题：{title}
  难度分布目标：easy {easy_pct}% / medium {medium_pct}% / hard {hard_pct}%

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
  "difficulty_distribution_score": 0.9,
  "summary": "总体评价文字"
}}
"""


async def _run_reviewer(questions: list[dict], blueprint: dict) -> dict[str, Any]:
    parts: list[str] = []
    for q in questions:
        entities_str = ", ".join(q.get("entities") or [q.get("entity", "")])
        line = f"[{q['id']}] ({q['type']}/{q['objective']}, entities: {entities_str}, reasoning_steps: {q.get('reasoning_steps', '?')}) {q['question']}"
        if q.get("options"):
            line += f"\n  选项: {'; '.join(q['options'])}"
        line += f"\n  答案: {q['answer']}"
        parts.append(line)
    questions_text = "\n\n".join(parts)

    dw = blueprint.get("difficulty_weights") or {"easy": 0.2, "medium": 0.6, "hard": 0.2}
    easy_pct = round(dw.get("easy", 0.2) * 100)
    medium_pct = round(dw.get("medium", 0.6) * 100)
    hard_pct = round(dw.get("hard", 0.2) * 100)

    prompt = _REVIEWER_PROMPT_TMPL.format(
        title=blueprint.get("title", ""),
        easy_pct=easy_pct,
        medium_pct=medium_pct,
        hard_pct=hard_pct,
        total=len(questions),
        questions_text=questions_text[:4000],
    )

    raw = await llm_chat_model_func(prompt, system_prompt=_REVIEWER_SYSTEM)
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"ReviewerAgent returned no JSON: {raw[:300]}")
    result = json.loads(m.group())
    result.setdefault("difficulty_distribution_score", 0.5)
    return result


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
以下是评审未通过的题目及对应问题，请逐题修复或标记删除。
蓝图难度分布目标：{difficulty_weights}

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
        entities_str = ", ".join(q.get("entities") or [q.get("entity", "")])
        item = (
            f"题目ID={q['id']} (entities: {entities_str}, type: {q['type']}/{q['objective']})\n"
            f"题目: {q['question']}\n"
            f"答案: {q['answer']}\n"
            f"评审问题: {issues_text}\n"
            f"改进建议: {suggestion}"
        )
        failed_items_parts.append(item)

    prompt = _FIXER_PROMPT_TMPL.format(
        difficulty_weights=(
            lambda dw: f"easy {round(dw.get('easy', 0.2)*100)}% / medium {round(dw.get('medium', 0.6)*100)}% / hard {round(dw.get('hard', 0.2)*100)}%"
        )(blueprint.get("difficulty_weights") or {}),
        failed_items="\n\n".join(failed_items_parts),
    )
    try:
        raw = await llm_chat_model_func(prompt, system_prompt=_FIXER_SYSTEM)
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
    used_entities: set[str] = set()
    for q in questions:
        used_entities.update(q.get("entities") or [q.get("entity", "")])
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
        # Build difficulty map from blueprint questions for replenishment
        bp_q_map: dict[int, dict] = {
            bq["id"]: bq for bq in (blueprint.get("questions") or [])
        }
        deleted_difficulties: list[str] = [
            bp_q_map.get(qid, {}).get("difficulty", "medium") for qid in sorted(delete_ids)
        ]
        spare_candidates = [c for c in candidates if c["name"] not in used_entities]
        next_id = max((q["id"] for q in questions), default=0) + 1
        for i, cand in enumerate(spare_candidates[:deleted_count]):
            slot_diff = deleted_difficulties[i] if i < len(deleted_difficulties) else "medium"
            new_q = await generate_one(
                entity_name=cand["name"],
                context=cand["context"],
                q_type="short_answer",
                q_id=next_id + i,
                score=cand["score"],
                chunk_ids=cand["chunk_ids"],
                objective="knowledge",
                entity_names=[cand["name"]],
                difficulty=slot_diff,
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
    entity_names: list[str],
    q_type: str,
    objective: str,
    q_id: int,
    extra_requirements: str = "",
    current_question: str = "",
    difficulty: str = "medium",
) -> dict[str, Any] | None:
    """Regenerate a single question using course RAG context.

    Returns a question dict (same schema as generate_one output) or None on failure.
    Called from the edu-agent FastAPI server (async context).
    """
    primary_entity = entity_names[0] if entity_names else ""
    # Build query: prefer entity_names, fall back to current_question excerpt or extra_requirements
    query_parts = [p for p in [primary_entity, extra_requirements] if p.strip()]
    if not query_parts and current_question:
        # Strip HTML tags simply and take first 80 chars as query seed
        import re as _re
        plain = _re.sub(r"<[^>]+>", "", current_question).strip()
        query_parts = [plain[:80]]
    query = " ".join(query_parts).strip() or "知识点"

    raw = await course_aquery_data(course_id, query, mode="local", top_k=10)
    data = raw.get("data") or {}

    chunks: list[dict] = list(data.get("chunks") or [])
    chunk_map: dict[str, str] = {}
    for ch in chunks:
        cid = str(ch.get("id") or ch.get("chunk_id") or "").strip()
        content = str(ch.get("content") or "").strip()
        if cid and content:
            chunk_map[cid] = content

    entity_chunk_ids = [
        cid for cid, content in chunk_map.items()
        if primary_entity and primary_entity.lower() in content.lower()
    ][:3] or list(chunk_map.keys())[:3]

    context = "\n\n".join(chunk_map[cid] for cid in entity_chunk_ids if cid in chunk_map)[:2500]

    if not context:
        logger.warning(
            "regenerate_one_question: no context found for entities={} course={}",
            entity_names, course_id,
        )
        return None

    # Append teacher's extra requirements and the current question as reference
    if current_question:
        import re as _re
        plain_q = _re.sub(r"<[^>]+>", "", current_question).strip()
        context = f"{context}\n\n【当前题目（仅供参考，请生成不同的新题目）】\n{plain_q[:500]}"
    if extra_requirements:
        context = f"{context}\n\n【教师要求】\n{extra_requirements}"

    return await generate_one(
        entity_name=primary_entity,
        context=context,
        q_type=q_type,
        q_id=q_id,
        score=1.0,
        chunk_ids=entity_chunk_ids,
        objective=objective,
        entity_names=entity_names,
        difficulty=difficulty,
    )


# ---------------------------------------------------------------------------
# Teacher custom question completion
# ---------------------------------------------------------------------------

_COMPLETE_SYSTEM_PROMPT = """\
你是一位专业的考试助教。老师已经写好了题目的题干，你的任务是在**完全不改动题干**的前提下，
补全题目的其余部分（选项、标准答案、解析）。

规则：
1. question 字段必须与老师提供的题干原文一字不差地输出，不得修改、润色或简化
2. 对于单选题（single_choice）：生成四个选项（A/B/C/D），只有一个正确答案，answer 为正确选项字母
3. 对于多选题（multi_choice）：生成四个选项（A/B/C/D），有两个或以上正确答案，answer 为各正确选项字母用逗号连接（如 "A,C"）
4. 对于填空题（fill_blank）：options 为空数组，answer 为填入空白处的正确答案
5. 对于简答题（short_answer）：options 为空数组，answer 为完整参考答案（3-8句话）
6. explanation 字段：说明答案为什么正确；若提供了课程原文可引用，否则根据知识推理说明
7. 若老师已提供参考答案，优先以其为基准（可适当完善表述，但不得与其矛盾）
8. 输出必须是严格的 JSON 格式，不要包含任何其他文字或 markdown 代码块标记
"""

_TYPE_LABELS_COMPLETE = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
}


def _build_complete_prompt(
    stem: str, answer_hint: str, q_type: str, context: str = ""
) -> str:
    label = _TYPE_LABELS_COMPLETE.get(q_type, "题目")
    prompt = (
        f"以下是老师写好的{label}题干（请原样保留，不得改动）：\n\n"
        f"「{stem}」\n\n"
    )
    if answer_hint:
        prompt += f"老师提供的参考答案（请以此为基准）：{answer_hint}\n\n"
    if context:
        prompt += f"【课程相关知识（供生成解析参考）】\n{context}\n\n"
    prompt += "请在完全保留上方题干的前提下，补全以下字段：\n"
    if q_type == "single_choice":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "<原样复制老师题干>", '
            '"options": ["A. ...", "B. ...", "C. ...", "D. ..."], '
            '"answer": "A", '
            '"explanation": "解析"}'
        )
    elif q_type == "multi_choice":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "<原样复制老师题干>", '
            '"options": ["A. ...", "B. ...", "C. ...", "D. ..."], '
            '"answer": "A,C", '
            '"explanation": "解析"}'
        )
    elif q_type == "fill_blank":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "<原样复制老师题干>", '
            '"options": [], '
            '"answer": "填入答案", '
            '"explanation": "解析"}'
        )
    else:  # short_answer
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "<原样复制老师题干>", '
            '"options": [], '
            '"answer": "完整参考答案", '
            '"explanation": "评分要点"}'
        )
    return prompt


async def complete_teacher_question(
    course_id: str,
    entity_names: list[str],
    question_stem: str,
    answer_hint: str,
    q_type: str,
    objective: str,
    q_id: int,
    difficulty: str = "medium",
) -> dict[str, Any] | None:
    """Complete a teacher-written question stem with AI-generated options/answer/explanation.

    The question_stem is ALWAYS preserved exactly as written by the teacher.
    AI only generates: options (for MCQ), canonical answer, explanation.
    """
    primary_entity = entity_names[0] if entity_names else ""
    plain_stem = re.sub(r"<[^>]+>", "", question_stem).strip()
    query = f"{' '.join(entity_names[:2])} {plain_stem[:60]}".strip() or primary_entity or "知识点"

    raw = await course_aquery_data(course_id, query, mode="local", top_k=6)
    data = raw.get("data") or {}
    chunks: list[dict] = list(data.get("chunks") or [])
    chunk_map: dict[str, str] = {}
    for ch in chunks:
        cid = str(ch.get("id") or ch.get("chunk_id") or "").strip()
        content = str(ch.get("content") or "").strip()
        if cid and content:
            chunk_map[cid] = content
    chunk_ids = list(chunk_map.keys())[:3]
    context = "\n\n".join(chunk_map[cid] for cid in chunk_ids if cid in chunk_map)[:2000]

    prompt = _build_complete_prompt(plain_stem, answer_hint, q_type, context)
    parsed = await _call_question_llm(primary_entity or "自定义", q_type, prompt, _COMPLETE_SYSTEM_PROMPT)

    if parsed is None:
        logger.warning(
            "complete_teacher_question: LLM failed for stem={!r} course={}",
            plain_stem[:60], course_id,
        )
        return None

    return {
        "id":               q_id,
        "type":             q_type,
        "objective":        objective,
        "entities":         entity_names,
        "tags":             entity_names,
        "importance_score": 1.0,
        "reasoning_steps":  int(parsed.get("reasoning_steps") or {"easy": 1, "medium": 2, "hard": 3}.get(difficulty, 2)),
        # Always use teacher's original stem — never the LLM's rewrite
        "question":         plain_stem,
        "options":          parsed.get("options", []),
        "answer":           parsed.get("answer", "") or answer_hint,
        "explanation":      parsed.get("explanation", ""),
        "source_chunk_ids": chunk_ids[:2],
        "source_images":    [],
    }


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
    """Full pipeline: retrieve → slot → plan → generate → review → update DB.

    Runs the async pipeline in a dedicated event loop so it can be called
    from the synchronous Redis Stream worker.
    conn is a psycopg3 sync connection (autocommit=False).
    When structured_params is provided, lesson/weight params are extracted from it;
    both paths use the LLM Planner to assign entities per slot.
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

                # ── Step 1: Extract generation params (structured or NLP defaults) ─
                if structured_params is not None:
                    sp = structured_params
                    count = max(1, min(50, int(sp.get("count", 10))))
                    type_weights_raw: dict = sp.get("typeWeights") or DEFAULT_TYPE_WEIGHTS
                    obj_weights_raw: dict = sp.get("objectiveWeights") or DEFAULT_OBJECTIVE_WEIGHTS
                    dw_raw: dict = sp.get("difficultyWeights") or {"easy": 0.2, "medium": 0.6, "hard": 0.2}
                    tw_total = sum(type_weights_raw.values()) or 1.0
                    ow_total = sum(obj_weights_raw.values()) or 1.0
                    dw_total = sum(dw_raw.values()) or 1.0
                    tw = {k: v / tw_total for k, v in type_weights_raw.items()}
                    ow = {k: v / ow_total for k, v in obj_weights_raw.items()}
                    dw = {k: v / dw_total for k, v in dw_raw.items()}
                    lesson_names: list[str] = sp.get("lessonNames") or []
                    kp: list[str] = sp.get("knowledgePoints") or []
                    topic_parts = lesson_names + kp
                    topic_hint = "、".join(topic_parts[:4]) if topic_parts else ""
                else:
                    # NLP path: extract count from request text; use defaults for weights
                    _cm = re.search(r"(\d+)\s*[道题条]", teacher_request)
                    count = max(1, min(50, int(_cm.group(1)))) if _cm else 10
                    tw = DEFAULT_TYPE_WEIGHTS
                    ow = DEFAULT_OBJECTIVE_WEIGHTS
                    dw = {"easy": 0.2, "medium": 0.6, "hard": 0.2}
                    topic_hint = ""

                # ── Step 2: Retrieve entity candidates from course RAG ─────────
                candidates = await _retrieve_candidates(course_id, topic_hint, count)

                if not candidates:
                    raise RuntimeError(
                        "No entities or chunks found in course RAG. "
                        "Ensure course materials are indexed (status=READY)."
                    )

                # ── Step 3: Pre-compute slots (type + objective + difficulty) ──
                obj_fmt_pairs = assign_objective_format_pairs(
                    count, ow, tw, OBJECTIVE_FORMAT_COMPATIBILITY
                )
                difficulty_slots = _distribute_difficulty(count, dw)
                slots = _make_slots(obj_fmt_pairs, difficulty_slots)

                # ── Step 4: Planner — assign entities + focus per slot ─────────
                planner_candidates = candidates[:min(len(candidates), max(count * 2, 10), 40)]
                blueprint = await _run_planner(teacher_request, planner_candidates, slots, dw)
                logger.info("Blueprint ready: title={}", blueprint.get("title"))

                # ── Step 5: Merge slots into blueprint.questions + fallback/pad ─
                bp_qs: list[dict] = list(blueprint.get("questions") or [])
                if len(bp_qs) > count:
                    bp_qs = bp_qs[:count]
                used_bp_names: set[str] = {
                    n for q in bp_qs for n in (q.get("entity_names") or [])
                }
                for slot in slots[len(bp_qs):]:
                    fallback_cand = next(
                        (c for c in candidates if c["name"] not in used_bp_names),
                        candidates[0],
                    )
                    bp_qs.append({"entity_names": [fallback_cand["name"]], "focus": ""})
                    used_bp_names.add(fallback_cand["name"])
                # Stamp each slot's type/objective/difficulty onto blueprint questions
                for bq, slot in zip(bp_qs, slots):
                    bq["id"] = slot["id"]
                    bq["type"] = slot["type"]
                    bq["objective"] = slot["objective"]
                    bq["difficulty"] = slot["difficulty"]
                blueprint["questions"] = bp_qs

                logger.info(
                    "Blueprint JSON:\n{}",
                    json.dumps(blueprint, ensure_ascii=False, indent=2),
                )
                _db_update(pipe_conn, assignment_id, blueprint=blueprint)

                # Load course image map for source_images traceability
                course_image_map = _load_course_image_map(pipe_conn, course_id)
                _all_images: list[dict] = sorted(
                    [img for imgs in course_image_map.values() for img in imgs],
                    key=lambda x: x["page_idx"],
                )

                # ── Step 6: Generate questions per blueprint slot ──────────────
                sem = asyncio.Semaphore(settings.llm_max_async)

                async def _guarded(bq: dict) -> dict | None:
                    entity = _match_entity(
                        (bq.get("entity_names") or [""])[0],
                        candidates,
                    )
                    if entity is None or not entity.get("context"):
                        return None
                    async with sem:
                        return await generate_one(
                            entity_name=entity["name"],
                            context=entity["context"],
                            q_type=bq["type"],
                            q_id=bq["id"],
                            score=entity["score"],
                            chunk_ids=entity["chunk_ids"],
                            objective=bq["objective"],
                            entity_names=bq.get("entity_names") or [entity["name"]],
                            difficulty=bq["difficulty"],
                        )

                tasks = [_guarded(bq) for bq in blueprint["questions"]]

                raw_results = await asyncio.gather(*tasks, return_exceptions=True)
                questions: list[dict] = []
                for r in raw_results:
                    if isinstance(r, dict):
                        r.setdefault("score", 5)  # default point value per question
                        # propagate difficulty from the blueprint slot
                        r.setdefault("difficulty", bq["difficulty"])
                        questions.append(r)
                    elif isinstance(r, Exception):
                        logger.warning("Question generation task failed: {}", r)

                if not questions:
                    raise RuntimeError("All question generation tasks failed — check LLM logs.")

                # Re-number sequentially after filtering
                for seq, q in enumerate(questions, 1):
                    q["id"] = seq

                # Attach source_images: for each question, find images on adjacent pages
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
