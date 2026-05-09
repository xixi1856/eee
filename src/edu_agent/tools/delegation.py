"""Sub-agent delegation tool (A4 async)."""

from __future__ import annotations

import logging

from edu_agent.runtime_context import get_current_runtime
from edu_agent.subagent import SubAgent
from edu_agent.tool_payloads import tool_error
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.types import SubAgentConfig, ToolResult

logger = logging.getLogger(__name__)

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


async def _handle_delegate_task(args: dict) -> str:
    task = args.get("task", "")
    if not task:
        return tool_error("缺少必要参数：task")
    allowed_tools: list[str] = list(args.get("allowed_tools") or [])
    max_iterations = max(1, min(int(args.get("max_iterations", 5)), 10))

    cfg = SubAgentConfig(
        task=task,
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
    )
    try:
        result = await SubAgent(settings=get_current_runtime().settings).arun(cfg)
        if result.success:
            return ToolResult(
                tool_name=SCHEMA["name"],
                success=True,
                summary=result.summary,
                payload=result.payload,
            )
        return ToolResult(
            tool_name=SCHEMA["name"],
            success=False,
            summary="",
            error=result.error or "子 Agent 执行失败",
        )
    except Exception as exc:
        logger.error("delegate_task failed: %s", exc)
        return tool_error(str(exc))


toolset_registry.register(
    ToolSpec(
        name=SCHEMA["name"],
        description=SCHEMA["description"],
        input_schema=SCHEMA["parameters"],
        handler=_handle_delegate_task,
        toolset="delegation",
        permissions=[ToolPermission.EXTERNAL],
        emoji="🤖",
    )
)
