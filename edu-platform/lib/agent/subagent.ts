/**
 * SubAgent — isolated single-task delegation with tool whitelist.
 * Mirrors Python subagent.py. No recursion allowed.
 */

import OpenAI from "openai";
import type { Tool, TurnContext } from "./types";

const MAX_ITERATIONS = 6;
const RECURSION_BLACKLIST = new Set(["delegate_task"]);

const MINIMAL_SYSTEM = `你是一个专注的任务执行助手。请严格完成以下子任务，不要闲聊，输出简洁结构化的结果。`;

export type SubAgentConfig = {
  task: string;
  allowedTools: Tool[];
  /** Optional context (user/session IDs etc.) passed through for tool execution */
  ctx?: TurnContext;
  model?: string;
};

export type SubTaskResult = {
  success: boolean;
  summary: string;
  error?: string;
};

/** Execute a tool by name — tools must implement `execute(args, ctx)` */
export async function runSubAgent(
  client: OpenAI,
  model: string,
  config: SubAgentConfig,
  /** depth guard — set to 1 when already inside a subagent */
  depth: number = 0,
): Promise<SubTaskResult> {
  if (depth > 0) {
    return {
      success: false,
      summary: "",
      error: "禁止递归委派：SubAgent 内部不可再次调用 delegate_task。",
    };
  }

  const allowedTools = config.allowedTools.filter(
    (t) => !RECURSION_BLACKLIST.has(t.name),
  );

  const openaiTools = allowedTools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.parameters,
    },
  }));

  const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: MINIMAL_SYSTEM },
    { role: "user", content: config.task },
  ];

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const resp = await client.chat.completions.create({
      model: config.model ?? model,
      messages,
      tools: openaiTools.length > 0 ? openaiTools : undefined,
      tool_choice: openaiTools.length > 0 ? "auto" : undefined,
    });

    const msg = resp.choices[0]?.message;
    if (!msg) break;

    messages.push(msg as OpenAI.Chat.ChatCompletionMessageParam);

    if (msg.tool_calls && msg.tool_calls.length > 0) {
      for (const tc of msg.tool_calls) {
        if (tc.type !== "function") continue;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const tcAny = tc as any;
        const fnName: string = tcAny.function?.name ?? "";
        const fnArgs: string = tcAny.function?.arguments ?? "";
        const tool = allowedTools.find((t) => t.name === fnName);
        if (!tool) {
          messages.push({
            role: "tool",
            tool_call_id: tc.id,
            content: `Error: tool "${fnName}" not found`,
          });
          continue;
        }
        let args: Record<string, unknown> = {};
        try {
          args = JSON.parse(fnArgs) as Record<string, unknown>;
        } catch {
          // use empty args
        }
        let result: string;
        try {
          const raw = await tool.execute(args, config.ctx ?? ({} as TurnContext));
          result = typeof raw === "string" ? raw : JSON.stringify(raw);
        } catch (err) {
          result = `Error: ${err instanceof Error ? err.message : String(err)}`;
        }
        messages.push({
          role: "tool",
          tool_call_id: tc.id,
          content: result,
        });
      }
    } else {
      // Final text response
      return {
        success: true,
        summary: msg.content ?? "",
      };
    }

    if (resp.choices[0]?.finish_reason === "stop") break;
  }

  // Extract last assistant message
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const content = typeof lastAssistant?.content === "string" ? lastAssistant.content : "";
  return { success: true, summary: content };
}
