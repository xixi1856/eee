"""LLM-based evaluation tools.

Toolset: eval
Tools: hint_generator, score_essay, evaluate_code
"""

from __future__ import annotations

import asyncio
import json
import logging

from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SCHEMA_HINT_GENERATOR = {
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
}

_SCHEMA_SCORE_ESSAY = {
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
}

_SCHEMA_EVALUATE_CODE = {
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
}


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, system: str = "") -> str:
    """Call the configured LLM synchronously and return the text reply.

    Module-level so tests can patch via
    ``patch("edu_agent.tools.eval._call_llm", ...)``.
    """
    from edu_agent.providers.runtime import build_openai_client, resolve_provider_runtime
    from edu_agent.runtime_context import get_current_runtime

    ctx = get_current_runtime()
    rt = resolve_provider_runtime(ctx.settings, None, "auxiliary")
    client = build_openai_client(rt)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = client.chat.completions.create(
        model=rt.model,
        messages=messages,  # type: ignore[arg-type]
        temperature=rt.temperature,
        max_tokens=rt.max_tokens,
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Hint level instructions
# ---------------------------------------------------------------------------

_HINT_INSTRUCTIONS: dict[int, str] = {
    1: "给出一个非常轻微的提示，引发思考方向，绝不透露答案",
    2: "给出一个中等程度的提示，提供部分解题方向，但不揭示完整答案",
    3: "给出一个较详细的提示，接近但不直接给出答案，帮助学习者完成最后一步",
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _handle_hint_generator(args: dict) -> str:
    question = args.get("question", "")
    if not question:
        return tool_error("缺少必要参数：question")
    context: str = args.get("context", "")
    level = max(1, min(int(args.get("level", 1)), 3))
    instruction = _HINT_INSTRUCTIONS[level]
    system = f"你是一位苏格拉底式教学导师。{instruction}。只给出提示，不要给出完整答案。"
    ctx_part = f"\n\n背景信息：{context}" if context.strip() else ""
    prompt = (
        f"学习者遇到了以下问题：{question}{ctx_part}\n\n"
        f"请生成一个适合等级 {level} 的提示（中文）。"
    )
    try:
        hint = await asyncio.to_thread(_call_llm, prompt, system)
        return tool_result(hint)
    except Exception as exc:
        logger.error("hint_generator failed: %s", exc)
        return tool_error(str(exc))


async def _handle_score_essay(args: dict) -> str:
    question = args.get("question", "")
    student_answer = args.get("student_answer", "")
    if not question or not student_answer:
        return tool_error("缺少必要参数：question 和 student_answer")
    rubric: str = args.get("rubric", "")
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
        raw = await asyncio.to_thread(_call_llm, prompt, system)
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
            summary_text = raw
        return tool_result(summary_text, payload=raw)
    except Exception as exc:
        logger.error("score_essay failed: %s", exc)
        return tool_error(str(exc))


async def _handle_evaluate_code(args: dict) -> str:
    code = args.get("code", "")
    task_description = args.get("task_description", "")
    if not code or not task_description:
        return tool_error("缺少必要参数：code 和 task_description")
    language: str = args.get("language", "python")
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
        feedback = await asyncio.to_thread(_call_llm, prompt, system)
        return tool_result(feedback)
    except Exception as exc:
        logger.error("evaluate_code failed: %s", exc)
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_HINT_GENERATOR["name"],
        description=_SCHEMA_HINT_GENERATOR["description"],
        input_schema=_SCHEMA_HINT_GENERATOR["parameters"],
        handler=_handle_hint_generator,
        toolset="eval",
        permissions=[ToolPermission.NETWORK, ToolPermission.EXTERNAL],
        emoji="💡",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_SCORE_ESSAY["name"],
        description=_SCHEMA_SCORE_ESSAY["description"],
        input_schema=_SCHEMA_SCORE_ESSAY["parameters"],
        handler=_handle_score_essay,
        toolset="eval",
        permissions=[ToolPermission.NETWORK, ToolPermission.EXTERNAL],
        emoji="📊",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_EVALUATE_CODE["name"],
        description=_SCHEMA_EVALUATE_CODE["description"],
        input_schema=_SCHEMA_EVALUATE_CODE["parameters"],
        handler=_handle_evaluate_code,
        toolset="eval",
        permissions=[ToolPermission.NETWORK, ToolPermission.EXTERNAL],
        emoji="💻",
    )
)
