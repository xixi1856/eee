"""Generate examination questions from indexed documents using entity importance ranking.

Pipeline:
    1. Rank entities by importance = alpha * chunk_freq + beta * graph_degree
    2. Optionally filter to entities from specified source files
    3. For each top-K entity, retrieve original text context from stored chunks
    4. Call LLM to produce structured JSON (question / options / answer / explanation)
    5. Save results as JSON + Markdown
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from .config import settings
from .llm import llm_model_func

_GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"

# Default question type distribution – weights should sum to 1.0.
# Pass a custom dict to generate() to override at runtime.
DEFAULT_TYPE_WEIGHTS: dict[str, float] = {
    "single_choice": 0.4,
    "multi_choice":  0.1,
    "fill_blank":    0.3,
    "short_answer":  0.2,
}

_TYPE_LABELS: dict[str, str] = {
    "single_choice": "单选题",
    "multi_choice":  "多选题",
    "fill_blank":    "填空题",
    "short_answer":  "简答题",
}

OBJECTIVE_TYPES: dict[str, str] = {
    "knowledge":     "知识点题",
    "comprehension": "理解题",
    "application":   "应用题",
    "synthesis":     "综合题",
    "innovation":    "创新题",
}

DEFAULT_OBJECTIVE_WEIGHTS: dict[str, float] = {
    "knowledge":     0.30,
    "comprehension": 0.20,
    "application":   0.30,
    "synthesis":     0.10,
    "innovation":    0.10,
}

# Compatibility matrix: objective → {format → relative weight}
# Zero values completely exclude that (objective, format) combination,
# preventing nonsensical pairings regardless of user-supplied weights.
OBJECTIVE_FORMAT_COMPATIBILITY: dict[str, dict[str, float]] = {
    "knowledge":     {"single_choice": 0.5, "multi_choice": 0.1, "fill_blank": 0.4, "short_answer": 0.0},
    "comprehension": {"single_choice": 0.3, "multi_choice": 0.0, "fill_blank": 0.2, "short_answer": 0.5},
    "application":   {"single_choice": 0.2, "multi_choice": 0.2, "fill_blank": 0.0, "short_answer": 0.6},
    "synthesis":     {"single_choice": 0.1, "multi_choice": 0.2, "fill_blank": 0.0, "short_answer": 0.7},
    "innovation":    {"single_choice": 0.0, "multi_choice": 0.0, "fill_blank": 0.0, "short_answer": 1.0},
}

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _graph_degrees(graphml_path: Path) -> dict[str, int]:
    """Return {node_id: degree} counting both in- and out-edges."""
    if not graphml_path.exists():
        return {}
    try:
        tree = ET.parse(graphml_path)
    except ET.ParseError as exc:
        logger.warning(f"Could not parse GraphML: {exc}")
        return {}
    root = tree.getroot()
    ns = {"g": _GRAPHML_NS}
    graph_el = root.find("g:graph", ns)
    if graph_el is None:
        return {}
    degree: dict[str, int] = defaultdict(int)
    for edge_el in graph_el.findall("g:edge", ns):
        src = edge_el.get("source", "")
        tgt = edge_el.get("target", "")
        if src:
            degree[src] += 1
        if tgt:
            degree[tgt] += 1
    return dict(degree)


def _get_graph_neighbors(entity_name: str, graphml_path: Path, max_n: int = 3) -> list[str]:
    """Return names of entities directly connected to *entity_name* in the knowledge graph."""
    if not graphml_path.exists():
        return []
    try:
        tree = ET.parse(graphml_path)
    except ET.ParseError:
        return []
    root = tree.getroot()
    ns = {"g": _GRAPHML_NS}
    graph_el = root.find("g:graph", ns)
    if graph_el is None:
        return []
    neighbors: list[str] = []
    for edge_el in graph_el.findall("g:edge", ns):
        src = edge_el.get("source", "")
        tgt = edge_el.get("target", "")
        if src == entity_name and tgt and tgt != entity_name:
            neighbors.append(tgt)
        elif tgt == entity_name and src and src != entity_name:
            neighbors.append(src)
    seen: set[str] = set()
    unique: list[str] = []
    for n in neighbors:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique[:max_n]


# ---------------------------------------------------------------------------
# Entity ranking
# ---------------------------------------------------------------------------

def _rank_entities(
    file_paths: list[Path] | None,
    alpha: float,
    beta: float,
) -> list[dict[str, Any]]:
    """Return entities sorted by importance score (descending).

    Each item contains: name, score, chunk_ids, file_paths.
    If *file_paths* is provided, only entities whose chunks originate from
    those files are included (matched by stem substring, case-insensitive).
    """
    entity_chunks: dict = _load_json(settings.working_dir / "kv_store_entity_chunks.json")
    text_chunks: dict   = _load_json(settings.working_dir / "kv_store_text_chunks.json")
    degrees = _graph_degrees(settings.working_dir / "graph_chunk_entity_relation.graphml")

    allowed_stems: set[str] | None = None
    if file_paths:
        allowed_stems = {p.stem.lower() for p in file_paths}

    results: list[dict[str, Any]] = []

    for entity_name, meta in entity_chunks.items():
        all_chunk_ids: list[str] = meta.get("chunk_ids", [])

        if allowed_stems is not None:
            # Keep only chunks whose file_path stem contains an allowed stem
            relevant_ids = [
                cid for cid in all_chunk_ids
                if any(
                    stem in Path(text_chunks.get(cid, {}).get("file_path", "")).stem.lower()
                    for stem in allowed_stems
                )
            ]
            if not relevant_ids:
                continue
            chunk_freq = len(relevant_ids)
        else:
            relevant_ids = all_chunk_ids
            chunk_freq = meta.get("count", len(all_chunk_ids))

        degree = degrees.get(entity_name, 0)
        score = alpha * chunk_freq + beta * degree

        entity_file_paths = {
            text_chunks.get(cid, {}).get("file_path", "") for cid in relevant_ids
        }

        results.append({
            "name":       entity_name,
            "score":      score,
            "chunk_ids":  relevant_ids,
            "file_paths": list(entity_file_paths - {""}),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Context retrieval
# ---------------------------------------------------------------------------

def _pick_context(chunk_ids: list[str], text_chunks: dict, max_chars: int = 1500) -> str:
    """Concatenate up to 3 chunk texts, truncated to *max_chars*."""
    texts: list[str] = []
    for cid in chunk_ids[:3]:
        content = text_chunks.get(cid, {}).get("content", "").strip()
        if content:
            texts.append(content)
    combined = "\n\n---\n\n".join(texts)
    return combined[:max_chars]


def _pick_multi_entity_context(
    entity_name: str,
    entity_chunks: dict,
    text_chunks: dict,
    graphml_path: Path,
    objective: str,
) -> tuple[str, list[str], list[str]]:
    """Build context aggregating multiple entities based on *objective*.

    Returns (context_text, all_chunk_ids_used, entity_names_used).

    Context size and entity count depend on *objective*:
      - knowledge / comprehension / innovation: single entity, max 1500-2000 chars
      - application:  primary + up to 2 chunk-sharing neighbours, max 2500 chars
      - synthesis:    primary + up to 3 graph-connected entities, max 3000 chars
    """
    _MAX_CHARS: dict[str, int] = {
        "knowledge":     1500,
        "comprehension": 2000,
        "application":   2500,
        "synthesis":     3000,
        "innovation":    2000,
    }
    max_chars = _MAX_CHARS.get(objective, 1500)
    primary_chunk_ids: list[str] = entity_chunks.get(entity_name, {}).get("chunk_ids", [])

    if objective in ("knowledge", "comprehension", "innovation"):
        context = _pick_context(primary_chunk_ids, text_chunks, max_chars)
        return context, primary_chunk_ids[:3], [entity_name]

    # application: find entities sharing chunks with the primary entity
    if objective == "application":
        neighbour_names: list[str] = []
        primary_set = set(primary_chunk_ids)
        for ename, emeta in entity_chunks.items():
            if ename == entity_name:
                continue
            if primary_set & set(emeta.get("chunk_ids", [])):
                neighbour_names.append(ename)
            if len(neighbour_names) >= 2:
                break
    else:  # synthesis: use graph-connected neighbours
        neighbour_names = _get_graph_neighbors(entity_name, graphml_path, max_n=3)

    all_names = [entity_name] + neighbour_names
    seen_cids: set[str] = set()
    all_chunk_ids: list[str] = []
    for name in all_names:
        for cid in entity_chunks.get(name, {}).get("chunk_ids", [])[:2]:
            if cid not in seen_cids:
                seen_cids.add(cid)
                all_chunk_ids.append(cid)

    per_entity_chars = max_chars // max(len(all_names), 1)
    parts: list[str] = []
    for name in all_names:
        cids = entity_chunks.get(name, {}).get("chunk_ids", [])[:2]
        chunk_text = _pick_context(cids, text_chunks, per_entity_chars)
        if chunk_text:
            parts.append(f'\u300c\u5173\u4e8e“{name}”\u300d\n{chunk_text}')

    combined = "\n\n".join(parts)[:max_chars]
    return combined, all_chunk_ids[:6], all_names


# ---------------------------------------------------------------------------
# LLM prompt construction & generation
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一位专业的考试命题专家。
你的任务是根据提供的原文内容，为指定知识点出一道考试题目。

规则：
1. 题目内容和答案必须完全来自提供的原文，不得引入原文未提及的知识
2. 输出必须是严格的 JSON 格式，不要包含任何其他文字或 markdown 代码块标记
3. 单选题必须提供四个选项（A/B/C/D），只有一个正确答案
4. 多选题必须提供四个选项（A/B/C/D），有两个或以上正确答案，answer 为各正确选项字母用逗号连接（如 "A,C"）
5. 填空题答案应简短（1-5 个词或短语）
6. 简答题答案应完整但简洁（3-8 句话）
7. explanation 字段需引用原文中的相关句子作为佐证
8. 严禁在题目中引用文档结构信息：不得提及 ★ 符号、大纲、目录、章节列表、学习目标列表等
9. 严禁出现依赖不可见资源的表述：不得使用"如图所示"、"见下表"、"原文末尾提供的"、"附图"等措辞
10. 题目必须考查实质知识点，而非文档的组织形式或编写结构
11. 题干须自洽：考生只读题干与选项即可理解题意并完成作答，不得依赖「原文」「上文」「该材料」「本课讲义」等指向材料本身的笼统指代
12. 若题干需要背景事实，请用一至三句简短陈述直接写入题干（条件、数据、场景），禁止使用「如下图」「见上文」「材料所述」等未把信息说清楚的指代
"""

_DEIXIS_RETRY_USER_APPEND = """
【重要：上一版题干不合规，请重新输出整份 JSON】
上一版可能含有：指代原文/材料/图/表而未给出具体信息，或依赖不可见排版。
请重写：题干必须自洽，把所需关键事实直接写进题干；仍遵守系统规则中的题型与 JSON 格式要求。
"""

_OBJECTIVE_INSTRUCTIONS: dict[str, str] = {
    "knowledge": (
        "本题为【知识点题】，直接考查对单个知识点、定义或概念的记忆与基础理解，"
        "题干应明确，考查内容单一，解题步骤清晰。"
    ),
    "comprehension": (
        "本题为【理解题】，考查对概念或原理的深层理解，而不仅是记忆。"
        "可要求学生解释、对比或简要分析，答案需体现理解而非复述原文。"
    ),
    "application": (
        "本题为【应用题/情景题】，需在题干中引入下方提供的真实情境，"
        "要求学生在该情境中应用知识解决问题，重点考查分析与推理能力。"
    ),
    "synthesis": (
        "本题为【综合题】，涉及多个知识点的整合，考查综合运用能力。"
        "题目应跨越单一知识点，要求分析多个概念之间的关联，"
        "原文中已提供多个相关知识点的内容供参考。"
    ),
    "innovation": (
        "本题为【创新/开放题】，强调创造性思维，无固定标准答案。"
        "题干应引导学生提出自己的见解、方法或改进方案并论证可行性，"
        "评价重点在于思路的合理性和创造性。"
    ),
}

_SCENARIO_SYSTEM_PROMPT = """\
你是一位经验丰富的教学设计专家。
根据提供的原文内容和核心知识点，请生成一段用于出题的背景情境或多知识点关联分析。要求：
1. 内容必须来源于原文，不得引入原文未提及的知识
2. 情境必须是真实世界的工程、应用或生活场景（如系统故障排查、网络调试、软件开发决策等）
3. 严禁生成教学管理场景（如"教师编制大纲"、"学生填写学习目标"、"课程设计"等）
4. 若核心知识点属于教学管理术语（如"学习目标"、"课程大纲"、"教学目标"、"课时安排"），则拒绝生成，直接返回空字符串
5. 情境描述应语言简洁，150-250字
6. 直接输出情境描述文本，不要添加任何格式标记或 JSON
"""

# ---------------------------------------------------------------------------
# Meta-question filter
# ---------------------------------------------------------------------------

_META_QUESTION_PATTERN = re.compile(
    r"如图[所示]?|图\s*\d+|下图|上图|附图|\[图\]"
    r"|原文[末尾]*提供|文中提供|见[下上]?表|表\s*\d+"
    r"|★|大纲.*列表|学习目标.*列表|课程大纲中|教学大纲"
    r"|列表中.*作用|章节.*大纲",
    re.IGNORECASE,
)


def _is_meta_question(text: str) -> bool:
    """Return True if the question text references invisible resources or document structure."""
    return bool(_META_QUESTION_PATTERN.search(text))


async def _call_question_llm(
    entity_name: str,
    q_type: str,
    user_prompt: str,
    system_prompt: str,
) -> dict[str, Any] | None:
    """Single LLM round: return parsed question dict or None."""
    try:
        logger.debug("LLM prompt for '{}' ({}): {}...", entity_name, q_type, user_prompt[:2000])
        raw = await llm_model_func(user_prompt, system_prompt=system_prompt)
        logger.debug("LLM response for '{}': {}...", entity_name, raw[:1000])
    except Exception as exc:
        logger.warning(f"LLM call failed for '{entity_name}': {exc}")
        return None

    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning(f"No JSON found in response for '{entity_name}'")
        return None

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning(f"JSON parse error for '{entity_name}': {exc}")
        return None

    if not parsed.get("question") or not parsed.get("answer"):
        logger.warning(f"Incomplete question data for '{entity_name}', skipping")
        return None
    return parsed


_DIFFICULTY_STEPS: dict[str, int] = {"easy": 1, "medium": 2, "hard": 3}


def _build_prompt(entity: str, context: str, q_type: str, objective: str = "knowledge", difficulty: str = "medium") -> str:
    label = _TYPE_LABELS[q_type]
    obj_instruction = _OBJECTIVE_INSTRUCTIONS.get(objective, "")
    expected_steps = _DIFFICULTY_STEPS.get(difficulty, 2)

    prompt = (
        f'请根据以下原文内容，以"{entity}"为核心知识点，出一道{label}。\n\n'
        f"【考查目标】\n{obj_instruction}\n\n"
        f"【原文内容】\n{context}\n\n"
        f"【要求】\n"
        f"- 核心知识点：{entity}\n"
        f"- 题目类型：{label}\n"
        f"- 考查方向：{OBJECTIVE_TYPES.get(objective, '')}\n"
        f"- 目标难度：{difficulty}（期望推理步骤数：{expected_steps} 步）\n"
        f"  · easy = 1步：直接回忆/识别单一概念\n"
        f"  · medium = 2步：理解后推导，或将知识应用到给定场景\n"
        f"  · hard = 3步+：多跳推理，需连接多个中间结论或跨概念综合\n"
        f"- 题干必须自洽：禁止用「原文/上文/下图/见表/该材料」等指代而未给出具体信息；"
        f"需要的事实请用简短陈述直接写在题干中\n"
        f"- 请在 JSON 输出中如实填写 reasoning_steps 字段（你实际用了几步推理）\n"
    )

    if q_type == "single_choice":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "题目内容（不含选项）", '
            '"options": ["A. ...", "B. ...", "C. ...", "D. ..."], '
            '"answer": "A", '
            '"reasoning_steps": 2, '
            '"explanation": "解析，引用原文说明为什么选A"}'
        )
    elif q_type == "multi_choice":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "题目内容（不含选项）", '
            '"options": ["A. ...", "B. ...", "C. ...", "D. ..."], '
            '"answer": "A,C", '
            '"reasoning_steps": 2, '
            '"explanation": "解析，引用原文说明为什么选这几项"}'
        )
    elif q_type == "fill_blank":
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "题目内容，用______表示空白处", '
            '"options": [], '
            '"answer": "填入空白处的正确答案", '
            '"reasoning_steps": 1, '
            '"explanation": "解析，引用原文说明答案依据"}'
        )
    else:  # short_answer
        prompt += (
            '\n【输出格式（JSON）】\n'
            '{"question": "简答题问题内容", '
            '"options": [], '
            '"answer": "完整参考答案", '
            '"reasoning_steps": 2, '
            '"explanation": "评分要点，引用原文关键句"}'
        )

    return prompt


async def _generate_scenario(entity_name: str, context: str, objective: str) -> str:
    """First-stage LLM call: generate a scenario or analysis for application/synthesis.

    Returns a natural-language description (not JSON) to enrich the second-stage prompt.
    """
    if objective == "application":
        user_msg = (
            f'以下是关于"{entity_name}"的原文内容。\n\n{context}\n\n'
            f'请以"{entity_name}"为核心，构造一个贴近真实的情境（如用户遇到的问题、'
            f"系统运行场景等），该情境将用于后续命题。"
        )
    else:  # synthesis
        user_msg = (
            f"以下是涉及多个知识点的原文内容。\n\n{context}\n\n"
            f'请以"{entity_name}"为主线，梳理以上内容中多个知识点之间的关联与依赖关系，'
            f"该分析将用于后续综合题命题。"
        )
    try:
        scenario = await llm_model_func(user_msg, system_prompt=_SCENARIO_SYSTEM_PROMPT)
        logger.debug(f"Scenario generated for '{entity_name}' ({objective}): {len(scenario)} chars")
        return scenario
    except Exception as exc:
        logger.warning(f"Scenario generation failed for '{entity_name}': {exc}")
        return ""


async def _generate_one(
    entity_name: str,
    context: str,
    q_type: str,
    q_id: int,
    score: float,
    chunk_ids: list[str],
    objective: str = "knowledge",
    entity_names: list[str] | None = None,
    difficulty: str = "medium",
) -> dict[str, Any] | None:
    """Call LLM and parse one question. Returns None on any failure.

    For application/synthesis objectives uses a two-stage approach:
      Stage 1 – generate a scenario or multi-entity analysis (natural language).
      Stage 2 – generate the structured question based on the enriched context.
    """
    # Two-stage generation for application and synthesis
    if objective in ("application", "synthesis"):
        scenario = await _generate_scenario(entity_name, context, objective)
        enriched_context = f"{context}\n\n【情境/分析】\n{scenario}" if scenario else context
    else:
        enriched_context = context

    prompt = _build_prompt(entity_name, enriched_context, q_type, objective, difficulty)
    parsed = await _call_question_llm(entity_name, q_type, prompt, _SYSTEM_PROMPT)
    if parsed is None:
        return None

    question_text = str(parsed.get("question", ""))
    if _is_meta_question(question_text):
        logger.warning(
            "Meta-question pattern on first pass for '{}' ({}), retrying once: {}...",
            entity_name, q_type, question_text[:120],
        )
        retry_prompt = prompt + _DEIXIS_RETRY_USER_APPEND
        parsed = await _call_question_llm(entity_name, q_type, retry_prompt, _SYSTEM_PROMPT)
        if parsed is None:
            return None
        question_text = str(parsed.get("question", ""))
        if _is_meta_question(question_text):
            logger.warning(
                "Meta-question persists after retry for '{}': {}...",
                entity_name, question_text[:100],
            )
            return None

    return {
        "id":               q_id,
        "type":             q_type,
        "objective":        objective,
        "entities":         entity_names if entity_names is not None else [entity_name],
        "tags":             entity_names if entity_names is not None else [entity_name],
        "importance_score": round(score, 2),
        "reasoning_steps":  int(parsed.get("reasoning_steps") or _DIFFICULTY_STEPS.get(difficulty, 2)),
        "question":         question_text,
        "options":          parsed.get("options", []),
        "answer":           parsed.get("answer", ""),
        "explanation":      parsed.get("explanation", ""),
        "source_chunk_ids": chunk_ids[:2],
        "source_images":    [],
    }


# ---------------------------------------------------------------------------
# Type assignment
# ---------------------------------------------------------------------------

def _assign_types(total: int, type_weights: dict[str, float]) -> list[str]:
    """Distribute question types proportionally to *type_weights*."""
    types: list[str] = []
    for q_type, weight in type_weights.items():
        types.extend([q_type] * round(total * weight))
    # Adjust to exact total
    fallback = next(iter(type_weights))
    while len(types) < total:
        types.append(fallback)
    return types[:total]


def _assign_objective_format_pairs(
    total: int,
    obj_weights: dict[str, float],
    fmt_weights: dict[str, float],
    compatibility: dict[str, dict[str, float]],
) -> list[tuple[str, str]]:
    """Distribute (objective, format) pairs using a joint weighted distribution.

    score(obj, fmt) = obj_weight × fmt_weight × compatibility[obj][fmt]

    Pairs where compatibility == 0 are excluded entirely, preventing nonsensical
    combinations (e.g. innovation + fill_blank) regardless of user-supplied weights.
    Both the -w and -g CLI parameters therefore interact rather than stack.
    """
    joint: dict[tuple[str, str], float] = {}
    for obj, ow in obj_weights.items():
        for fmt, fw in fmt_weights.items():
            compat = compatibility.get(obj, {}).get(fmt, 0.0)
            s = ow * fw * compat
            if s > 0:
                joint[(obj, fmt)] = s

    if not joint:
        # Fallback: pick first valid combination from obj_weights priority
        for obj in obj_weights:
            for fmt in fmt_weights:
                if compatibility.get(obj, {}).get(fmt, 0.0) > 0:
                    return [(obj, fmt)] * total
        return [("knowledge", "single_choice")] * total

    total_score = sum(joint.values())
    normalized = {k: v / total_score for k, v in joint.items()}

    pairs: list[tuple[str, str]] = []
    for pair, weight in normalized.items():
        pairs.extend([pair] * round(total * weight))

    fallback_pair = max(normalized, key=lambda k: normalized[k])
    while len(pairs) < total:
        pairs.append(fallback_pair)
    return pairs[:total]


def _run_async_from_sync(coro_factory):
    """Run *coro_factory()* in a fresh event loop.

    Uses ``asyncio.run`` when no loop is running; otherwise runs the coroutine
    in a worker thread with its own loop (``asyncio.run`` cannot nest).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    def _in_thread() -> Any:
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_thread).result()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    file_paths: list[Path] | None = None,
    count: int = 20,
    alpha: float = 0.6,
    beta: float = 0.4,
    type_weights: dict[str, float] | None = None,
    objective_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Rank entities, call LLM to produce questions, return structured result.

    Args:
        file_paths:        Restrict scope to entities from these source files.
                           ``None`` means all indexed documents.
        count:             Target number of questions in the final output.
        alpha:             Weight for chunk-frequency in importance score.
        beta:              Weight for graph-degree in importance score.
        type_weights:      Mapping of question format → proportion.
                           Valid keys: single_choice, multi_choice, fill_blank, short_answer.
                           Defaults to ``DEFAULT_TYPE_WEIGHTS`` when ``None``.
        objective_weights: Mapping of cognitive objective → proportion.
                           Valid keys: knowledge, comprehension, application, synthesis, innovation.
                           Defaults to ``DEFAULT_OBJECTIVE_WEIGHTS`` when ``None``.
                           Interacts with type_weights via compatibility matrix – zero-compatibility
                           pairs are excluded regardless of weights.

    Returns:
        Dict with keys: generated_at, file_filter, total, questions.
    """
    fmt_weights = type_weights if type_weights is not None else DEFAULT_TYPE_WEIGHTS
    obj_weights = objective_weights if objective_weights is not None else DEFAULT_OBJECTIVE_WEIGHTS
    logger.info(f"Ranking entities (alpha={alpha}, beta={beta})…")
    ranked = _rank_entities(file_paths, alpha, beta)

    if not ranked:
        raise RuntimeError(
            "No entities found. Ensure documents are ingested and the knowledge "
            "base is populated (run 'rag ingest' or 'rag reindex' first)."
        )

    # Use up to 2× count candidates so we have backups for failed LLM calls
    top_k = min(len(ranked), count * 2)
    candidates = ranked[:top_k]
    logger.info(f"Selected top {top_k} entities from {len(ranked)} total for question generation")

    text_chunks: dict = _load_json(settings.working_dir / "kv_store_text_chunks.json")
    entity_chunks: dict = _load_json(settings.working_dir / "kv_store_entity_chunks.json")
    graphml_path = settings.working_dir / "graph_chunk_entity_relation.graphml"

    # Joint distribution assigns (objective, format) pairs via compatibility matrix
    obj_fmt_pairs = _assign_objective_format_pairs(
        top_k, obj_weights, fmt_weights, OBJECTIVE_FORMAT_COMPATIBILITY
    )
    obj_dist = {o: sum(1 for p in obj_fmt_pairs if p[0] == o) for o in obj_weights if any(p[0] == o for p in obj_fmt_pairs)}
    logger.info(f"Objective distribution across {top_k} candidates: {obj_dist}")

    async def _run_all() -> list[dict[str, Any] | None]:
        sem = asyncio.Semaphore(settings.llm_max_async)

        async def _guarded(entity: dict, q_objective: str, q_type: str, idx: int) -> dict[str, Any] | None:
            async with sem:
                context, ctx_chunk_ids, ctx_entity_names = _pick_multi_entity_context(
                    entity_name=entity["name"],
                    entity_chunks=entity_chunks,
                    text_chunks=text_chunks,
                    graphml_path=graphml_path,
                    objective=q_objective,
                )
                if not context:
                    logger.debug(f"No context found for entity '{entity['name']}', skipping")
                    return None
                return await _generate_one(
                    entity_name=entity["name"],
                    context=context,
                    q_type=q_type,
                    q_id=idx + 1,
                    score=entity["score"],
                    chunk_ids=ctx_chunk_ids,
                    objective=q_objective,
                    entity_names=ctx_entity_names,
                )

        tasks = [
            _guarded(candidates[i], obj_fmt_pairs[i][0], obj_fmt_pairs[i][1], i)
            for i in range(top_k)
        ]
        return list(await asyncio.gather(*tasks))

    logger.info(f"Generating questions with LLM (target: {count})…")
    raw_results = _run_async_from_sync(_run_all)

    questions = [q for q in raw_results if q is not None][:count]

    # Re-number sequentially after filtering
    for idx, q in enumerate(questions, 1):
        q["id"] = idx

    logger.success(f"Generated {len(questions)} / {count} questions successfully")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "file_filter":  [str(p) for p in file_paths] if file_paths else None,
        "total":        len(questions),
        "questions":    questions,
    }


def save_output(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Persist result dict as timestamped JSON and Markdown files.

    Returns:
        (json_path, md_path) absolute paths to the written files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"questions_{ts}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = output_dir / f"questions_{ts}.md"
    lines: list[str] = [
        f"# 题目集（共 {result['total']} 题）",
        f"\n生成时间：{result['generated_at']}",
    ]
    if result.get("file_filter"):
        lines.append(f"\n文件范围：{', '.join(result['file_filter'])}")
    lines.append("\n---\n")

    for q in result["questions"]:
        type_label = _TYPE_LABELS.get(q["type"], q["type"])
        obj_label = OBJECTIVE_TYPES.get(q.get("objective", ""), "")
        header = (
            f"## 第 {q['id']} 题  [{type_label}]  [{obj_label}]"
            if obj_label else
            f"## 第 {q['id']} 题  [{type_label}]"
        )
        lines.append(header)
        lines.append(f"\n*核心知识点：{', '.join(q.get('entities', [q.get('entity', '')]))}（重要性分：{q['importance_score']}）*\n")
        lines.append(f"**{q['question']}**\n")
        if q.get("options"):
            for opt in q["options"]:
                lines.append(f"- {opt}")
            lines.append("")
        lines.append(f"> **答案**：{q['answer']}\n")
        lines.append(f"> **解析**：{q['explanation']}\n")
        lines.append("---\n")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# Public aliases for assignment_gen (avoid duplicating implementation)
# ---------------------------------------------------------------------------

generate_one = _generate_one
assign_objective_format_pairs = _assign_objective_format_pairs
