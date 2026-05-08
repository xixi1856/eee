"""Sub-agent delegation tool.

Toolset: delegation
Tools: delegate_task
"""

from __future__ import annotations

import logging

from edu_agent.registry import registry, tool_result, tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = {
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
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_delegate_task(args: dict, **kw) -> str:
    task = args.get("task", "")
    if not task:
        return tool_error("缺少必要参数：task")
    allowed_tools: list[str] = list(args.get("allowed_tools") or [])
    max_iterations = max(1, min(int(args.get("max_iterations", 5)), 10))

    from edu_agent.subagent import SubAgent
    from edu_agent.types import SubAgentConfig

    cfg = SubAgentConfig(
        task=task,
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
    )
    try:
        result = SubAgent().run(cfg)
        if result.success:
            return tool_result(result.summary, payload=result.payload)
        return tool_error(result.error or "子 Agent 执行失败")
    except Exception as exc:
        logger.error("delegate_task failed: %s", exc)
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="delegate_task",
    schema=SCHEMA,
    handler=_handle_delegate_task,
    toolset="delegation",
    emoji="🤖",
)
