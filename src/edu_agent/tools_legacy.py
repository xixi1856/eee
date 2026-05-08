"""Tool definitions and handler implementations.

Each built-in tool exposes:
  - A JSON schema entry in ``TOOL_SCHEMAS``.
  - A Python handler registered to central ``edu_agent.registry`` via ``@_register``.
"""

import copy
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable

from edu_agent.registry import registry
from edu_agent.types import ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wikipedia in-memory cache  (process-scoped, no TTL needed)
# ---------------------------------------------------------------------------

_WIKI_CACHE: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Schema registry – fed directly to the OpenAI chat.completions.create() call
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
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
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
        },
    },
    {
        "type": "function",
        "function": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_document",
            "description": (
                "解析 PDF、图片或 Word 文档（使用 MinerU），将其转换为可检索的 Markdown 格式。"
                "在导入知识库之前需要先解析文档。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "待解析的文件路径或目录路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hint_generator",
            "description": (
                "为学习者遇到的问题生成苏格拉底式分级提示，引导思考而不直接给出答案。"
                "当学习者表示卡住、需要提示或要求引导时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "学习者遇到困难的问题或题目",
                    },
                    "context": {
                        "type": "string",
                        "description": "与问题相关的背景信息（可选）",
                    },
                    "level": {
                        "type": "integer",
                        "description": "提示等级：1（轻微引导）、2（部分方向）、3（接近答案）",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_essay",
            "description": (
                "对学习者的书面作答或论述题答案进行评分，给出得分和改进建议。"
                "当学习者提交作答希望获得反馈时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "原始题目或问题",
                    },
                    "student_answer": {
                        "type": "string",
                        "description": "学习者的作答内容",
                    },
                    "rubric": {
                        "type": "string",
                        "description": "评分标准（可选，为空时使用通用标准）",
                    },
                },
                "required": ["question", "student_answer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_code",
            "description": (
                "评估学习者提交的代码，检查正确性、代码质量和边界情况，给出建设性反馈。"
                "当学习者提交代码并希望获得代码审查或反馈时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "学习者提交的代码",
                    },
                    "task_description": {
                        "type": "string",
                        "description": "编程任务描述或要求",
                    },
                    "language": {
                        "type": "string",
                        "description": "编程语言（默认 python）",
                    },
                },
                "required": ["code", "task_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "将复杂子任务委派给隔离的子 Agent 执行。子 Agent 拥有独立上下文，"
                "不继承当前会话历史。适用于需要多步工具调用但不希望污染主对话历史的场景。"
                "不可在子 Agent 内再次调用 delegate_task（禁止递归委派）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "自然语言描述的子任务，要求明确、可独立完成",
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "子 Agent 可使用的工具名称列表（白名单）。"
                            "空列表表示仅依赖 LLM 能力，不调用任何工具。"
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "子 Agent 最大迭代轮次上限（默认 5，最大 10）",
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_search",
            "description": (
                "从维基百科检索某个概念或术语的解释，用于补充知识库中未涵盖的通用知识点。"
                "当知识库查询结果不足或需要百科级背景知识时调用。"
                "若返回结果为歧义页，请根据候选词列表用更具体的词重新调用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的概念名称或关键词，建议使用精确术语",
                    },
                    "lang": {
                        "type": "string",
                        "enum": ["zh", "en"],
                        "description": "查询语言：zh（中文，默认）或 en（英文）",
                    },
                    "summary_only": {
                        "type": "boolean",
                        "description": (
                            "是否只返回摘要（默认 true，节省 token）。"
                            "设为 false 时额外返回前 3 个章节内容。"
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "返回内容的最大字符数，默认 500。summary_only=false 时各 section 按比例截断。",
                    },
                },
                "required": ["query"],
            },
        },
    },
    # ------------------------------------------------------------------
    # Web tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "通过 Tavily API（优先）或 DuckDuckGo 搜索互联网，返回相关网页的标题、URL 和摘要。"
                "适用于查询实时资讯、政策热点、最新事件等知识库未涵盖的内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
                    "max_results": {"type": "integer", "description": "返回结果数量（默认 5，最多 10）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "抓取指定 URL 的网页正文内容（静态 HTML），提取纯文本。"
                "配合 web_search 使用：先搜索得到 URL，再用此工具获取详细内容。"
                "不支持需要 JavaScript 渲染的页面。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页 URL"},
                    "max_chars": {"type": "integer", "description": "返回正文最大字符数（默认 8000）"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ollama_web_search",
            "description": (
                "使用 Ollama 官方 Web Search API（https://ollama.com/api/web_search）搜索互联网。"
                "需要设置环境变量 OLLAMA_API_KEY（在 https://ollama.com/settings/keys 创建）。"
                "与 web_search 互补：web_search 使用 Tavily/DuckDuckGo，此工具使用 Ollama 自有搜索服务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
                    "max_results": {"type": "integer", "description": "返回结果数量（默认 5，最多 10）"},
                },
                "required": ["query"],
            },
        },
    },
    # ------------------------------------------------------------------
    # File I/O tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将文本内容写入本地文件（限 output/ 目录内）。"
                "可用于保存爬取的资讯、生成的报告或整理好的笔记。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 output/ 目录的文件路径，如 news/2026-05-07.md",
                    },
                    "content": {"type": "string", "description": "要写入的文本内容"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "写入模式：overwrite（覆盖，默认）或 append（追加）",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取 output/ 目录内的本地文件内容。"
                "可用于查看之前保存的资讯或报告。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 output/ 目录的文件路径",
                    },
                    "max_chars": {"type": "integer", "description": "返回最大字符数（默认 16000）"},
                },
                "required": ["path"],
            },
        },
    },
    # ------------------------------------------------------------------
    # Skills tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出当前所有可用的教学技能（name + description 索引）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_skill",
            "description": (
                "查看某个技能的完整 SKILL.md 内容（Tier1）。"
                "对于目录型技能，还可通过 file_path 读取 scripts/ 或 references/ 中的附件文件（Tier2）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名称（来自 list_skills）"},
                    "file_path": {
                        "type": "string",
                        "description": "（可选）目录型技能内的附件相对路径，如 scripts/fetch.py",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skill",
            "description": (
                "创建或编辑技能文件。使用 create 创建新技能，edit 覆盖现有技能内容。"
                "技能保存后下一轮对话即可通过 list_skills / view_skill 使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "edit"],
                        "description": "操作类型",
                    },
                    "name": {"type": "string", "description": "技能名称（英文，用于文件名）"},
                    "content": {"type": "string", "description": "SKILL.md 的完整内容（含 frontmatter）"},
                },
                "required": ["action", "name", "content"],
            },
        },
    },
    # ------------------------------------------------------------------
    # Cron scheduling tool
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "cron_job",
            "description": (
                "管理定时任务：创建、列出、删除或立即触发一次。"
                "创建后 Agent 将按计划自动执行 prompt，结果保存到 output/cron/ 目录。"
                "schedule 示例：'every 1h'、'every 30m'、'0 9 * * *'（每天 9 点）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "delete", "trigger"],
                        "description": "操作：create 创建 | list 列出 | delete 删除 | trigger 立即执行",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "（create 必填）任务提示词，Agent 将按此执行",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "（create 必填）调度表达式，如 'every 1h' 或 '0 9 * * *'",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "（delete/trigger 必填）任务 ID",
                    },
                },
                "required": ["action"],
            },
        },
    },
]

_SCHEMA_BY_NAME: dict[str, dict] = {
    s["function"]["name"]: s for s in TOOL_SCHEMAS
}


def refresh_tool_schemas() -> None:
    """Keep list object stable while syncing schemas from registry."""
    latest = registry.get_schemas()
    TOOL_SCHEMAS[:] = latest


def _register(name: str) -> Callable:
    """Decorator to register a function as the handler for *name*."""

    def decorator(fn: Callable) -> Callable:
        schema = _SCHEMA_BY_NAME.get(name)
        if schema is None:
            raise KeyError(f"missing schema for tool {name}")
        registry.register(
            name=name,
            schema=copy.deepcopy(schema),
            handler=fn,
            description=schema["function"].get("description", name),
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@_register("knowledge_query")
def _handle_knowledge_query(question: str, mode: str = "hybrid") -> ToolResult:
    """Delegate question to the RAG engine and return a formatted summary."""
    try:
        from rag_mvp.engine import query  # lazy import to keep startup fast

        answer = query(question=question, mode=mode, with_refs=False)
        if not answer:
            return ToolResult(
                tool_name="knowledge_query",
                success=True,
                summary="知识库中暂无相关信息。",
            )
        if isinstance(answer, dict):
            text = answer.get("answer", str(answer))
        else:
            text = str(answer)
        return ToolResult(
            tool_name="knowledge_query",
            success=True,
            summary=text,
            payload=answer,
        )
    except Exception as exc:
        logger.error("knowledge_query failed: %s", exc)
        return ToolResult(
            tool_name="knowledge_query",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("generate_quiz")
def _handle_generate_quiz(
    count: int = 5,
    question_type: str = "mixed",
) -> ToolResult:
    """Generate quiz questions from the knowledge base."""
    count = max(1, min(int(count), 20))  # clamp to [1, 20]

    try:
        from rag_mvp.question_gen import (  # lazy import
            DEFAULT_OBJECTIVE_WEIGHTS,
            DEFAULT_TYPE_WEIGHTS,
            generate,
        )

        # Map simplified question_type to type_weights
        if question_type == "mixed":
            type_weights = None  # uses DEFAULT_TYPE_WEIGHTS
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
        return ToolResult(
            tool_name="generate_quiz",
            success=True,
            summary=summary,
            payload=result,
        )
    except Exception as exc:
        logger.error("generate_quiz failed: %s", exc)
        return ToolResult(
            tool_name="generate_quiz",
            success=False,
            summary="",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch tool call through the central registry."""
    return registry.dispatch(name, args)


# ---------------------------------------------------------------------------
# Additional tool handlers
# ---------------------------------------------------------------------------


@_register("build_mindmap")
def _handle_build_mindmap(source: str, refine: bool = False) -> ToolResult:
    """Generate a mindmap from a Markdown file or folder of Markdown files.

    Args:
        source: Path to a ``.md`` file or a directory containing ``.md`` files.
        refine: Whether to use the LLM-refine pass (slower but richer output).
    """
    try:
        from rag_mvp.mindmap import build_structure_mindmap  # lazy import

        out_paths = build_structure_mindmap(source, refine=refine)
        if not out_paths:
            return ToolResult(
                tool_name="build_mindmap",
                success=True,
                summary="思维导图生成完毕，但未找到可输出的文件路径。",
            )
        path_list = "\n".join(str(p) for p in out_paths)
        return ToolResult(
            tool_name="build_mindmap",
            success=True,
            summary=f"思维导图已生成，共 {len(out_paths)} 个文件：\n{path_list}",
            payload=[str(p) for p in out_paths],
        )
    except Exception as exc:
        logger.error("build_mindmap failed: %s", exc)
        return ToolResult(
            tool_name="build_mindmap",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("parse_document")
def _handle_parse_document(path: str) -> ToolResult:
    """Parse a document (PDF/image/DOCX) or a folder of documents with MinerU.

    Parsed Markdown and extracted images are saved under ``output/parsed/``.

    Args:
        path: Absolute or relative path to a single file or directory.
    """
    try:
        from pathlib import Path as _Path

        from rag_mvp.engine import parse_file, parse_folder  # lazy import

        target = _Path(path)
        if target.is_dir():
            parse_folder(target)
            summary = f"目录 '{path}' 下的文档已全部解析完毕。"
        else:
            parse_file(target)
            summary = f"文档 '{path}' 已解析完毕。"
        return ToolResult(tool_name="parse_document", success=True, summary=summary)
    except Exception as exc:
        logger.error("parse_document failed: %s", exc)
        return ToolResult(
            tool_name="parse_document",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("ingest_document")
def _handle_ingest_document(path: str) -> ToolResult:
    """Ingest a parsed Markdown file or folder into the RAG knowledge base.

    Args:
        path: Absolute or relative path to a ``.md`` file or directory.
    """
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
        return ToolResult(tool_name="ingest_document", success=True, summary=summary)
    except Exception as exc:
        logger.error("ingest_document failed: %s", exc)
        return ToolResult(
            tool_name="ingest_document",
            success=False,
            summary="",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Synchronous LLM helper (used by evaluation tools)
# ---------------------------------------------------------------------------


def _call_llm(prompt: str, system: str = "") -> str:
    """Call the configured LLM synchronously and return the text reply.

    Lazy-imports settings so tests can patch before the first call.
    This is a module-level function so tests can patch it via
    ``patch("edu_agent.tools._call_llm", ...)``.
    """
    from openai import OpenAI  # local import keeps startup fast
    from rag_mvp.config import settings

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Phase 3 evaluation handlers
# ---------------------------------------------------------------------------

_HINT_INSTRUCTIONS: dict[int, str] = {
    1: "给出一个非常轻微的提示，引发思考方向，绝不透露答案",
    2: "给出一个中等程度的提示，提供部分解题方向，但不揭示完整答案",
    3: "给出一个较详细的提示，接近但不直接给出答案，帮助学习者完成最后一步",
}


@_register("hint_generator")
def _handle_hint_generator(
    question: str,
    context: str = "",
    level: int = 1,
) -> ToolResult:
    """Generate a Socratic hint for a question at the requested level.

    Args:
        question: The question or problem the learner is stuck on.
        context:  Optional background information.
        level:    Hint depth 1–3 (clamped automatically).
    """
    level = max(1, min(int(level), 3))
    instruction = _HINT_INSTRUCTIONS[level]
    system = f"你是一位苏格拉底式教学导师。{instruction}。只给出提示，不要给出完整答案。"
    ctx_part = f"\n\n背景信息：{context}" if context.strip() else ""
    prompt = (
        f"学习者遇到了以下问题：{question}{ctx_part}\n\n"
        f"请生成一个适合等级 {level} 的提示（中文）。"
    )
    try:
        hint = _call_llm(prompt, system)
        return ToolResult(tool_name="hint_generator", success=True, summary=hint)
    except Exception as exc:
        logger.error("hint_generator failed: %s", exc)
        return ToolResult(
            tool_name="hint_generator",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("score_essay")
def _handle_score_essay(
    question: str,
    student_answer: str,
    rubric: str = "",
) -> ToolResult:
    """Score a learner's written answer and return structured feedback.

    Args:
        question:       The original question or prompt.
        student_answer: The learner's written response.
        rubric:         Optional scoring criteria; generic rubric used when empty.
    """
    rubric_part = f"\n评分标准：{rubric}" if rubric.strip() else ""
    system = (
        "你是一位严谨、公正且富有鼓励性的教学评估专家。"
        '请以 JSON 格式返回评分结果，格式为：{"score": int, "summary": str, "strengths": str, "improvements": str}。'
    )
    prompt = (
        f"题目：{question}{rubric_part}\n\n"
        f"学生回答：{student_answer}\n\n"
        "请给出 0–100 分的评分，并提供总体评价、优点和改进建议。"
    )
    try:
        raw = _call_llm(prompt, system)
        try:
            data = json.loads(raw)
            score = data.get("score", "N/A")
            summary_text = (
                f"**评分：{score}/100**\n\n"
                f"{data.get('summary', '')}\n\n"
                f"**优点：** {data.get('strengths', '')}\n\n"
                f"**改进建议：** {data.get('improvements', '')}"
            )
        except (json.JSONDecodeError, AttributeError):
            # Plain-text fallback: use raw LLM reply directly
            summary_text = raw
        return ToolResult(
            tool_name="score_essay",
            success=True,
            summary=summary_text,
            payload=raw,
        )
    except Exception as exc:
        logger.error("score_essay failed: %s", exc)
        return ToolResult(
            tool_name="score_essay",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("evaluate_code")
def _handle_evaluate_code(
    code: str,
    task_description: str,
    language: str = "python",
) -> ToolResult:
    """Evaluate learner-submitted code for correctness and quality.

    Args:
        code:             The learner's source code.
        task_description: Description of what the code should do.
        language:         Programming language (default: python).
    """
    system = "你是一位资深编程教学导师，擅长以鼓励的方式指导学生改进代码。"
    prompt = (
        f"编程语言：{language}\n"
        f"任务要求：{task_description}\n\n"
        f"学生代码：\n```{language}\n{code}\n```\n\n"
        "请从以下几个维度给出建设性、鼓励性的评估反馈：\n"
        "1. 正确性（代码是否实现了任务要求）\n"
        "2. 代码质量（可读性、命名、结构）\n"
        "3. 边界情况处理\n"
        "4. 改进建议"
    )
    try:
        feedback = _call_llm(prompt, system)
        return ToolResult(tool_name="evaluate_code", success=True, summary=feedback)
    except Exception as exc:
        logger.error("evaluate_code failed: %s", exc)
        return ToolResult(
            tool_name="evaluate_code",
            success=False,
            summary="",
            error=str(exc),
        )


@_register("delegate_task")
def _handle_delegate_task(
    task: str,
    allowed_tools: list[str] | None = None,
    max_iterations: int = 5,
) -> ToolResult:
    """Delegate a sub-task to an isolated SubAgent and return a summary.

    Args:
        task:            Natural-language description of the sub-task.
        allowed_tools:   Whitelist of tools the sub-agent may call.
        max_iterations:  Max iterations for the sub-agent (clamped to [1, 10]).
    """
    from edu_agent.subagent import SubAgent
    from edu_agent.types import SubAgentConfig

    max_iterations = max(1, min(int(max_iterations), 10))
    cfg = SubAgentConfig(
        task=task,
        allowed_tools=list(allowed_tools or []),
        max_iterations=max_iterations,
    )
    try:
        result = SubAgent().run(cfg)
        if result.success:
            return ToolResult(
                tool_name="delegate_task",
                success=True,
                summary=result.summary,
                payload=result.payload,
            )
        return ToolResult(
            tool_name="delegate_task",
            success=False,
            summary="",
            error=result.error,
        )
    except Exception as exc:
        logger.error("delegate_task failed: %s", exc)
        return ToolResult(
            tool_name="delegate_task",
            success=False,
            summary="",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Wikipedia search
# ---------------------------------------------------------------------------

# Phrases that indicate a disambiguation page in various languages
_DISAMBIG_MARKERS = ("may refer to", "可以指", "可以是", "可能指")


@_register("wikipedia_search")
def _handle_wikipedia_search(
    query: str,
    lang: str = "zh",
    summary_only: bool = True,
    max_chars: int = 500,
) -> ToolResult:
    """Fetch a Wikipedia article summary (and optionally sections) for *query*.

    Handles:
    - In-memory cache to avoid redundant network calls.
    - zh → en fallback when the Chinese article is missing.
    - Disambiguation pages: returns candidate list so the LLM can refine.
    """
    import wikipediaapi
    from rag_mvp.config import settings

    def _build_wiki(language: str) -> wikipediaapi.Wikipedia:
        proxy = settings.http_proxy or None
        kwargs: dict[str, Any] = {
            "user_agent": "EduAgent/1.0",
            "language": language,
            "extract_format": wikipediaapi.ExtractFormat.WIKI,
            "timeout": 20.0,
        }
        if proxy:
            kwargs["proxy"] = proxy
        return wikipediaapi.Wikipedia(**kwargs)

    def _is_disambiguation(page: wikipediaapi.WikipediaPage) -> bool:
        summary_head = page.summary[:400]
        if any(marker in summary_head for marker in _DISAMBIG_MARKERS):
            return True
        # Secondary check via categories
        for cat in page.categories:
            if "disambiguation" in cat.lower() or "消歧义" in cat:
                return True
        return False

    def _format_content(page: wikipediaapi.WikipediaPage) -> str:
        if summary_only:
            return page.summary[:max_chars]
        # Summary + first 3 sections
        section_budget = max_chars // 3
        parts = [page.summary[:max_chars]]
        for section in list(page.sections)[:3]:
            if section.text.strip():
                parts.append(f"### {section.title}\n{section.text[:section_budget]}")
        return "\n\n".join(parts)

    def _fetch(language: str) -> ToolResult:
        cache_key = f"{language}:{query}:{summary_only}:{max_chars}"
        if cache_key in _WIKI_CACHE:
            logger.debug("wikipedia_search cache hit: %s", cache_key)
            return ToolResult(
                tool_name="wikipedia_search",
                success=True,
                summary=_WIKI_CACHE[cache_key],
            )

        wiki = _build_wiki(language)
        try:
            page = wiki.page(query)
        except Exception as exc:
            logger.warning("wikipedia_search network error (%s): %s", language, exc)
            return ToolResult(
                tool_name="wikipedia_search",
                success=False,
                summary="",
                error=f"网络请求失败：{exc}",
            )

        if not page.exists():
            return ToolResult(
                tool_name="wikipedia_search",
                success=False,
                summary="",
                error=f"未在 {language} 维基百科中找到词条「{query}」",
            )

        if _is_disambiguation(page):
            candidates = list(page.links.keys())[:10]
            candidate_str = "\n".join(f"- {c}" for c in candidates)
            msg = (
                f"[歧义页] 「{query}」在维基百科中为歧义词条，可能指代以下内容，"
                f"请使用更具体的词重新调用 wikipedia_search：\n{candidate_str}"
            )
            logger.info("wikipedia_search disambiguation: %s", query)
            return ToolResult(
                tool_name="wikipedia_search",
                success=True,
                summary=msg,
            )

        content = _format_content(page)
        _WIKI_CACHE[cache_key] = content
        logger.info(
            "wikipedia_search fetched %d chars for「%s」(%s)", len(content), query, language
        )
        return ToolResult(
            tool_name="wikipedia_search",
            success=True,
            summary=content,
        )

    result = _fetch(lang)
    # zh fallback: if page not found, retry in English
    if not result.success and lang == "zh":
        logger.info("wikipedia_search zh→en fallback for「%s」", query)
        result = _fetch("en")
        if result.success and not result.summary.startswith("[歧义页]"):
            # Annotate so the LLM knows this is the English version
            result = ToolResult(
                tool_name="wikipedia_search",
                success=True,
                summary=f"[来源：英文维基百科]\n{result.summary}",
            )
    return result


# ---------------------------------------------------------------------------
# SSRF guard (shared by web_fetch and ollama_web_search)
# ---------------------------------------------------------------------------

import ipaddress  # noqa: E402
import urllib.parse  # noqa: E402


def _is_ssrf_url(url: str) -> bool:
    """Return True if *url* targets a private/loopback/link-local address."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        # Reject obvious local hostnames
        if host.lower() in ("localhost", "localhost.localdomain"):
            return True
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        # Not a numeric IP – allow (DNS resolves later; we do a best-effort check)
        host_lower = (urllib.parse.urlparse(url).hostname or "").lower()
        return host_lower in ("localhost",) or host_lower.endswith(".local")


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Scrape DuckDuckGo HTML results (no API key required)."""
    import urllib.parse
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "kl": "cn-zh"}
    try:
        resp = httpx.post(url, data=params, headers=_DDG_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("DDG search failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select(".result__body")[:max_results]:
        title_el = item.select_one(".result__title")
        url_el = item.select_one(".result__url")
        snippet_el = item.select_one(".result__snippet")
        title = title_el.get_text(strip=True) if title_el else ""
        link = url_el.get_text(strip=True) if url_el else ""
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title or link:
            if link and not link.startswith("http"):
                link = "https://" + link
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


@_register("web_search")
def _handle_web_search(query: str, max_results: int = 5) -> ToolResult:
    max_results = max(1, min(int(max_results), 10))
    results: list[dict] = []

    # Try Tavily first
    try:
        import os
        from rag_mvp.config import settings
        tavily_key = settings.tavily_api_key or os.environ.get("TAVILY_API_KEY")
        if tavily_key:
            import httpx
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": max_results},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                })
    except Exception as exc:
        logger.info("Tavily search failed, falling back to DDG: %s", exc)

    if not results:
        results = _ddg_search(query, max_results)

    if not results:
        return ToolResult(tool_name="web_search", success=False, summary="", error="搜索未返回任何结果")

    lines = [f"**搜索结果：{query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r['title']}**")
        lines.append(f"   🔗 {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return ToolResult(tool_name="web_search", success=True, summary="\n".join(lines), payload=results)


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

@_register("web_fetch")
def _handle_web_fetch(url: str, max_chars: int = 8000) -> ToolResult:
    if _is_ssrf_url(url):
        return ToolResult(tool_name="web_fetch", success=False, summary="", error="拒绝访问内部地址（SSRF 防护）")
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return ToolResult(tool_name="web_fetch", success=False, summary="", error="缺少依赖：httpx 和 beautifulsoup4")

    try:
        resp = httpx.get(url, headers=_DDG_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return ToolResult(tool_name="web_fetch", success=False, summary="", error=f"请求失败：{exc}")

    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    body = "\n\n".join(paragraphs)

    if title:
        text = f"# {title}\n\n{body}"
    else:
        text = body

    text = text[:max_chars]
    return ToolResult(tool_name="web_fetch", success=True, summary=text)


# ---------------------------------------------------------------------------
# ollama_web_search
# ---------------------------------------------------------------------------

@_register("ollama_web_search")
def _handle_ollama_web_search(
    query: str,
    max_results: int = 5,
) -> ToolResult:
    """Call Ollama's official Web Search API (https://ollama.com/api/web_search).

    Requires OLLAMA_API_KEY environment variable set to a key from
    https://ollama.com/settings/keys
    """
    import os
    import httpx

    max_results = max(1, min(int(max_results), 10))
    api_key = os.environ.get("OLLAMA_API_KEY", "")
    if not api_key:
        return ToolResult(
            tool_name="ollama_web_search",
            success=False,
            summary="",
            error="未设置 OLLAMA_API_KEY 环境变量，请在 https://ollama.com/settings/keys 创建 API Key 后设置",
        )

    try:
        resp = httpx.post(
            "https://ollama.com/api/web_search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": max_results},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return ToolResult(
            tool_name="ollama_web_search",
            success=False,
            summary="",
            error=f"Ollama Web Search API 返回错误 {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except Exception as exc:
        return ToolResult(
            tool_name="ollama_web_search",
            success=False,
            summary="",
            error=f"请求失败: {exc}",
        )

    results = data.get("results", [])
    if not results:
        return ToolResult(tool_name="ollama_web_search", success=True, summary="搜索未返回结果", payload=[])

    lines = [f"**Ollama Web Search：{query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r.get('title', '(无标题)')}**")
        lines.append(f"   🔗 {r.get('url', '')}")
        content = r.get("content", "").strip()
        if content:
            lines.append(f"   {content[:300]}{'…' if len(content) > 300 else ''}")
        lines.append("")
    summary = "\n".join(lines)
    return ToolResult(tool_name="ollama_web_search", success=True, summary=summary, payload=results)


# ---------------------------------------------------------------------------
# write_file / read_file  (sandboxed to output/)
# ---------------------------------------------------------------------------

def _resolve_output_path(path: str, base: str = "output") -> tuple[Any, str | None]:
    """Resolve *path* under *base*, return (resolved_path, error_or_None)."""
    import os
    base_path = (Path(base)).resolve()
    candidate = (base_path / path).resolve()
    if not str(candidate).startswith(str(base_path)):
        return None, f"路径越界，拒绝访问: {path}"
    return candidate, None


@_register("write_file")
def _handle_write_file(path: str, content: str, mode: str = "overwrite") -> ToolResult:
    resolved, err = _resolve_output_path(path)
    if err:
        return ToolResult(tool_name="write_file", success=False, summary="", error=err)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    file_mode = "a" if mode == "append" else "w"
    try:
        resolved.write_text(content, encoding="utf-8") if file_mode == "w" else \
            resolved.open("a", encoding="utf-8").write(content)
        size = resolved.stat().st_size
        return ToolResult(
            tool_name="write_file",
            success=True,
            summary=f"已写入 {size} 字节 → {resolved}",
            payload={"path": str(resolved), "bytes": size},
        )
    except OSError as exc:
        return ToolResult(tool_name="write_file", success=False, summary="", error=str(exc))


@_register("read_file")
def _handle_read_file(path: str, max_chars: int = 16000) -> ToolResult:
    resolved, err = _resolve_output_path(path)
    if err:
        return ToolResult(tool_name="read_file", success=False, summary="", error=err)
    if not resolved.exists():
        return ToolResult(tool_name="read_file", success=False, summary="", error=f"文件不存在: {path}")
    try:
        text = resolved.read_text(encoding="utf-8")[:max_chars]
        return ToolResult(tool_name="read_file", success=True, summary=text)
    except OSError as exc:
        return ToolResult(tool_name="read_file", success=False, summary="", error=str(exc))


# ---------------------------------------------------------------------------
# list_skills / view_skill / manage_skill
# ---------------------------------------------------------------------------

@_register("list_skills")
def _handle_list_skills() -> ToolResult:
    from edu_agent.skills_loader import load_skill_entries
    from rag_mvp.config import settings as _settings  # to find skills_dir

    # Use default skills dir; agent config not accessible here, use env or default
    import os
    skills_dir = os.environ.get("EDU_SKILLS_DIR", "skills")
    entries = load_skill_entries(skills_dir)
    if not entries:
        return ToolResult(tool_name="list_skills", success=True, summary="暂无可用技能。")
    lines = ["**可用技能列表：**\n"]
    for e in entries:
        desc = f" — {e.description}" if e.description else ""
        badge = " *(始终注入)*" if e.always_inject else ""
        lines.append(f"• **{e.name}**{desc}{badge}")
    return ToolResult(tool_name="list_skills", success=True, summary="\n".join(lines), payload=[e.name for e in entries])


@_register("view_skill")
def _handle_view_skill(name: str, file_path: str = "") -> ToolResult:
    from edu_agent.skills_loader import load_skill_entries, read_skill_file
    import os
    skills_dir = os.environ.get("EDU_SKILLS_DIR", "skills")
    entries = {e.name: e for e in load_skill_entries(skills_dir)}
    if name not in entries:
        return ToolResult(tool_name="view_skill", success=False, summary="", error=f"技能不存在: {name}")
    entry = entries[name]
    if file_path:
        if entry.skill_dir is None:
            return ToolResult(tool_name="view_skill", success=False, summary="", error="该技能为平铺格式，不支持附件访问")
        content = read_skill_file(entry.skill_dir, file_path)
        return ToolResult(tool_name="view_skill", success=True, summary=content)
    return ToolResult(tool_name="view_skill", success=True, summary=entry.body, payload={"name": name, "path": str(entry.path)})


# Patterns forbidden in agent-authored skill content
_SKILL_THREAT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"os\.system\s*\(",
        r"subprocess\.(call|run|Popen)\s*\(",
        r"eval\s*\(",
        r"exec\s*\(",
        r"__import__\s*\(",
        r"open\s*\(.+['\"]w['\"]",
    ]
]


@_register("manage_skill")
def _handle_manage_skill(action: str, name: str, content: str) -> ToolResult:
    import os
    from edu_agent.skills_loader import invalidate_cache

    skills_dir = Path(os.environ.get("EDU_SKILLS_DIR", "skills"))
    # Validate name (alphanumeric + underscore + hyphen only)
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return ToolResult(tool_name="manage_skill", success=False, summary="", error="技能名称只允许字母、数字、下划线和连字符")

    # Security scan
    for pat in _SKILL_THREAT_PATTERNS:
        if pat.search(content):
            return ToolResult(
                tool_name="manage_skill",
                success=False,
                summary="",
                error=f"内容包含不允许的代码模式（安全扫描失败）: {pat.pattern}",
            )

    # Determine target path: prefer directory-style if skill_dir already exists
    skill_dir = skills_dir / name
    if skill_dir.is_dir():
        target = skill_dir / "SKILL.md"
    else:
        target = skills_dir / f"{name}.md"

    if action == "create" and target.exists():
        return ToolResult(tool_name="manage_skill", success=False, summary="", error=f"技能已存在，请使用 action='edit' 修改: {name}")
    if action == "edit" and not target.exists():
        # Allow creating via edit if not found
        pass

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        invalidate_cache()
        return ToolResult(
            tool_name="manage_skill",
            success=True,
            summary=f"技能 '{name}' 已{'创建' if action == 'create' else '更新'} → {target}",
            payload={"name": name, "path": str(target)},
        )
    except OSError as exc:
        return ToolResult(tool_name="manage_skill", success=False, summary="", error=str(exc))


# ---------------------------------------------------------------------------
# cron_job  (delegates to cron.CronManager)
# ---------------------------------------------------------------------------

@_register("cron_job")
def _handle_cron_job(
    action: str,
    prompt: str = "",
    schedule: str = "",
    job_id: str = "",
) -> ToolResult:
    try:
        from edu_agent.cron import CronManager
    except ImportError as exc:
        return ToolResult(tool_name="cron_job", success=False, summary="", error=f"Cron 模块未安装: {exc}")

    mgr = CronManager()

    if action == "list":
        jobs = mgr.list_jobs()
        if not jobs:
            return ToolResult(tool_name="cron_job", success=True, summary="当前没有定时任务。")
        lines = ["**定时任务列表：**\n"]
        for j in jobs:
            lines.append(f"• **{j['id']}** `{j['schedule']}` — {j['prompt'][:60]}…")
            lines.append(f"  状态: {j['status']} | 上次执行: {j.get('last_run', '从未')}")
        return ToolResult(tool_name="cron_job", success=True, summary="\n".join(lines), payload=jobs)

    if action == "create":
        if not prompt or not schedule:
            return ToolResult(tool_name="cron_job", success=False, summary="", error="create 操作需要 prompt 和 schedule 参数")
        job = mgr.add_job(prompt=prompt, schedule=schedule)
        return ToolResult(
            tool_name="cron_job",
            success=True,
            summary=f"定时任务已创建 ✅\nID: **{job['id']}**\n调度: `{schedule}`\n任务: {prompt[:80]}",
            payload=job,
        )

    if action in ("delete", "trigger"):
        if not job_id:
            return ToolResult(tool_name="cron_job", success=False, summary="", error=f"{action} 操作需要 job_id 参数")
        if action == "delete":
            ok = mgr.delete_job(job_id)
            if ok:
                return ToolResult(tool_name="cron_job", success=True, summary=f"任务 {job_id} 已删除。")
            return ToolResult(tool_name="cron_job", success=False, summary="", error=f"未找到任务: {job_id}")
        # trigger
        result_text = mgr.trigger_job(job_id)
        return ToolResult(tool_name="cron_job", success=True, summary=result_text)

    return ToolResult(tool_name="cron_job", success=False, summary="", error=f"未知操作: {action}")


# Ensure schemas include all registered built-ins.
refresh_tool_schemas()
