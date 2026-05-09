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
                "type": "string",
                "enum": ["personal", "course", "all"],
                "description": "personal=个人RAG | course=课程RAG(B2桩) | all=合并",
            },
            "course_id": {
                "type": "string",
                "description": "课程 ID（sources 含 course 时建议填写；B2 前为占位结果）",
            },
        },
        "required": ["question"],
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

async def _handle_knowledge_query(args: dict) -> str:
    question = args.get("question")
    if not question:
        return tool_error("缺少必要参数：question")
    mode = args.get("mode", "hybrid")
    sources = (args.get("sources") or "all").strip().lower()
    if sources not in ("personal", "course", "all"):
        sources = "all"
    course_id = (args.get("course_id") or "").strip()
    if not course_id:
        try:
            ctx = get_current_runtime()
            if ctx.course_id:
                course_id = str(ctx.course_id).strip()
        except RuntimeError:
            pass
    if sources == "all" and not course_id:
        sources = "personal"

    results: list[dict[str, Any]] = []
    personal_text = ""

    if sources in ("personal", "all"):
        try:
            from rag_mvp.engine import query

            answer = query(question=question, mode=mode, with_refs=False)
            if answer:
                if isinstance(answer, dict):
                    personal_text = str(answer.get("answer", answer))
                else:
                    personal_text = str(answer)
                results.append(
                    {
                        "origin": "personal",
                        "chunk_id": "rag-mvp",
                        "text": personal_text[:8000],
                        "document_title": None,
                        "relevance_score": 1.0,
                    }
                )
        except Exception as exc:
            logger.error("knowledge_query personal leg failed: %s", exc)
            return tool_error(str(exc))

    if sources in ("course", "all"):
        if not course_id and sources == "course":
            return tool_error("sources=course 时必须提供 course_id")
        if course_id:
            results.append(
                {
                    "origin": "course",
                    "chunk_id": "stub-course-chunk",
                    "text": "[课程 RAG 占位：PostgreSQL 接入前由 B2 实现]",
                    "course_id": course_id,
                    "document_title": None,
                    "relevance_score": 0.0,
                }
            )

    if not results:
        return tool_result("知识库中暂无相关信息。")

    summary = json.dumps(results, ensure_ascii=False, indent=2)
    display = personal_text or results[0].get("text", summary)
    return tool_result(display[:12000], payload=results)


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
