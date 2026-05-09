"""Cron scheduling tool (A4 async)."""

from __future__ import annotations

import logging

from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry

logger = logging.getLogger(__name__)

SCHEMA = {
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
}


async def _handle_cron_job(args: dict) -> str:
    action = args.get("action", "")
    if not action:
        return tool_error("缺少必要参数：action")

    try:
        from edu_agent.cron import CronManager
    except ImportError as exc:
        return tool_error(f"Cron 模块未安装: {exc}")

    mgr = CronManager()

    if action == "list":
        jobs = mgr.list_jobs()
        if not jobs:
            return tool_result("当前没有定时任务。")
        lines = ["**定时任务列表：**\n"]
        for j in jobs:
            lines.append(f"• **{j['id']}** `{j['schedule']}` — {j['prompt'][:60]}…")
            lines.append(f"  状态: {j['status']} | 上次执行: {j.get('last_run', '从未')}")
        return tool_result("\n".join(lines), payload=jobs)

    if action == "create":
        prompt: str = args.get("prompt", "")
        schedule: str = args.get("schedule", "")
        if not prompt or not schedule:
            return tool_error("create 操作需要 prompt 和 schedule 参数")
        job = mgr.add_job(prompt=prompt, schedule=schedule)
        return tool_result(
            f"定时任务已创建 ✅\nID: **{job['id']}**\n调度: `{schedule}`\n任务: {prompt[:80]}",
            payload=job,
        )

    if action in ("delete", "trigger"):
        job_id: str = args.get("job_id", "")
        if not job_id:
            return tool_error(f"{action} 操作需要 job_id 参数")
        if action == "delete":
            ok = mgr.delete_job(job_id)
            if ok:
                return tool_result(f"任务 {job_id} 已删除。")
            return tool_error(f"未找到任务: {job_id}")
        result_text = mgr.trigger_job(job_id)
        return tool_result(result_text)

    return tool_error(f"未知操作: {action}")


toolset_registry.register(
    ToolSpec(
        name=SCHEMA["name"],
        description=SCHEMA["description"],
        input_schema=SCHEMA["parameters"],
        handler=_handle_cron_job,
        toolset="scheduling",
        permissions=[ToolPermission.WRITE],
        emoji="⏰",
    )
)
