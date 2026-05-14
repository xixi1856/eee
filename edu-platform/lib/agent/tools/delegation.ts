/**
 * Delegation tool: delegate_task
 * Spins up an isolated SubAgent to handle a sub-task.
 */

import OpenAI from "openai";
import type { Tool, TurnContext } from "../types";
import { runSubAgent } from "../subagent";
import { toolRegistry } from "./registry";

export const delegateTaskTool: Tool = {
  name: "delegate_task",
  description:
    "将复杂子任务委派给隔离的子 Agent 执行。子 Agent 拥有独立上下文，" +
    "不继承当前会话历史。适用于需要多步工具调用但不希望污染主对话历史的场景。" +
    "不可在子 Agent 内再次调用 delegate_task（禁止递归委派）。",
  requiresApproval: true,
  approvalReason: "此操作将启动一个子 Agent 来执行独立子任务，子 Agent 可调用多个工具。",
  category: "dangerous",
  parameters: {
    type: "object",
    properties: {
      task: {
        type: "string",
        description: "自然语言描述的子任务，要求明确、可独立完成",
      },
      allowed_tools: {
        type: "array",
        items: { type: "string" },
        description:
          "子 Agent 可使用的工具名称列表（白名单）。空列表表示仅依赖 LLM 能力。",
      },
    },
    required: ["task"],
  },
  async execute(args: Record<string, unknown>, ctx: TurnContext): Promise<string> {
    const task = typeof args.task === "string" ? args.task.trim() : "";
    if (!task) return JSON.stringify({ error: "缺少必要参数：task" });

    const allowedToolNames = Array.isArray(args.allowed_tools)
      ? (args.allowed_tools as unknown[]).filter((t) => typeof t === "string")
      : ([] as string[]);

    const allowedTools = (allowedToolNames as string[])
      .map((n) => toolRegistry.get(n))
      .filter(Boolean) as NonNullable<ReturnType<typeof toolRegistry.get>>[];

    const apiKey = process.env.OPENAI_API_KEY ?? process.env.LLM_API_KEY ?? "";
    const baseURL = process.env.LLM_BASE_URL || undefined;
    const model = process.env.LLM_MODEL ?? "gpt-4o-mini";
    const client = new OpenAI({ apiKey, baseURL });

    const result = await runSubAgent(client, model, { task, allowedTools, ctx }, 0);

    if (result.success) {
      return result.summary;
    }
    return JSON.stringify({ error: result.error ?? "子 Agent 执行失败" });
  },
};
