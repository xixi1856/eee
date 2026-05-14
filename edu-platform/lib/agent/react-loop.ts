/**
 * ReAct Loop — TS Agent core inference loop.
 * Emits B3SseEvent data lines into a ReadableStream<Uint8Array>.
 */

import OpenAI from "openai";
import { getRedis } from "@/lib/redis";
import type { Message, TurnContext, AgentConfig, ToolCitation } from "./types";
import type { ToolRegistry } from "./tool-registry";
import type { MemoryCoordinator } from "./memory/memory-coordinator";
import type { PromptBuilder } from "./prompt-builder";
import type { SkillEntry } from "./skills-loader";
import type { LearnerProfile } from "./memory/types";
import { ContextManager } from "./context-manager";

// ---- B3 SSE helpers ---------------------------------------------------------

const _enc = new TextEncoder();

type B3Event =
  | { type: "text"; content: string }
  | ({ type: "citation" } & ToolCitation)
  | { type: "tool_call"; name: string; tool_call_id?: string }
  | { type: "tool_result"; name: string; success?: boolean; duration_ms?: number }
  | { type: "done"; tokens?: number | null; exec_time_ms?: number | null; error?: string }
  | { type: "trace"; event: string; payload?: Record<string, unknown> }
  | {
      type: "require_approval";
      tool_call_id: string;
      tool_name: string;
      args_preview: Record<string, unknown>;
      approval_key: string;
      reason: string;
    }
  | { type: "approval_resolved"; tool_call_id: string; approved: boolean };

function sseData(ev: B3Event): Uint8Array {
  return _enc.encode(`data: ${JSON.stringify(ev)}\n\n`);
}

// ---- Types ------------------------------------------------------------------

export type ReactLoopOptions = {
  userMessage: string;
  config: AgentConfig;
  toolRegistry: ToolRegistry;
  ctx: TurnContext;
  coordinator: MemoryCoordinator;
  promptBuilder: PromptBuilder;
  skills: SkillEntry[];
  profile: LearnerProfile | null;
  memoryBlock: string;
  /** Prior conversation history (from SessionStore, excludes new user message) */
  history: Message[];
};

type PendingToolCall = { id: string; name: string; args: string };

// ---- Approval gate helpers -------------------------------------------------

const APPROVAL_TTL_SECONDS = 90;
const APPROVAL_POLL_MS = 500;
const APPROVAL_TIMEOUT_MS = 60_000;

/** Writes a pending approval record to Redis and returns the key. */
async function createApprovalRecord(
  sessionId: string,
  toolCallId: string,
  userId: string,
): Promise<string> {
  const key = `agent:approval:${sessionId}:${toolCallId}`;
  const redis = await getRedis();
  await redis.set(
    key,
    JSON.stringify({ approved: null, userId }),
    { EX: APPROVAL_TTL_SECONDS },
  );
  return key;
}

/** Polls Redis until the user responds or the timeout expires.
 *  Returns true if approved, false if denied or timed out. */
async function waitForApproval(
  approvalKey: string,
  timeoutMs = APPROVAL_TIMEOUT_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  const redis = await getRedis();
  while (Date.now() < deadline) {
    await new Promise<void>((r) => setTimeout(r, APPROVAL_POLL_MS));
    const raw = await redis.get(approvalKey).catch(() => null);
    if (!raw) return false; // key expired or deleted = deny
    try {
      const record = JSON.parse(raw) as { approved: boolean | null };
      if (record.approved === true) return true;
      if (record.approved === false) return false;
    } catch {
      return false;
    }
  }
  // Timed out — deny by default (least-privilege)
  await redis.del(approvalKey).catch(() => {});
  return false;
}

/** Truncates string values in an args object to protect against very long payloads. */
function sanitiseArgsPreview(args: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(args)) {
    out[k] = typeof v === "string" && v.length > 120 ? v.slice(0, 120) + "…" : v;
  }
  return out;
}

// ---- Main entry -------------------------------------------------------------

/**
 * Creates a ReadableStream<Uint8Array> that runs the ReAct loop and emits
 * B3SseEvent data lines. The stream closes after the `done` event.
 */
export function createReActStream(opts: ReactLoopOptions): ReadableStream<Uint8Array> {
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();

  void _runLoop(writer, opts).catch(async (err) => {
    const error = err instanceof Error ? err.message : String(err);
    console.error("[ReActLoop] unhandled error:", err);
    await writer.write(sseData({ type: "done", error })).catch(() => {});
    await writer.close().catch(() => {});
  });

  return readable;
}

async function _runLoop(
  writer: WritableStreamDefaultWriter<Uint8Array>,
  opts: ReactLoopOptions,
): Promise<void> {
  const startMs = Date.now();
  const { config, toolRegistry, ctx, coordinator, promptBuilder, skills, profile, memoryBlock } =
    opts;

  const apiKey = process.env.OPENAI_API_KEY ?? process.env.LLM_API_KEY ?? "";
  const baseURL = process.env.LLM_BASE_URL || undefined;
  const client = new OpenAI({ apiKey, baseURL });

  // Build system prompt
  const systemPrompt = promptBuilder.buildSystemPrompt(
    config.systemPrompt,
    skills,
    memoryBlock,
    profile,
    ctx,
  );

  // Compress history to fit within the configured context window before building messages.
  const ctxMgr = new ContextManager(config.maxContextTokens);
  const compressedHistory = ctxMgr.compress(opts.history);

  // Prepare messages: system + (compressed) history + new user message
  const loopMsgs: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: systemPrompt },
    ...compressedHistory.map(_toOpenAIParam),
    { role: "user", content: _buildUserContent(opts) },
  ];

  const schemas = toolRegistry.getSchemas();
  let totalTokens: number | null = null;
  let streamError: string | undefined;

  // Emit trace start
  if (ctx.debugTrace) {
    await writer.write(
      sseData({ type: "trace", event: "loop_start", payload: { sessionId: ctx.sessionId } }),
    );
  }

  try {
    for (let iter = 0; iter < config.maxIterations; iter++) {
      const pendingTcs = new Map<number, PendingToolCall>();
      let assistantText = "";

      // Stream LLM response
      const stream = await client.chat.completions.create({
        model: config.model,
        messages: loopMsgs,
        tools: schemas.length > 0 ? schemas : undefined,
        tool_choice: schemas.length > 0 ? "auto" : undefined,
        stream: true,
        stream_options: { include_usage: true },
      });

      for await (const chunk of stream) {
        const delta = chunk.choices[0]?.delta;
        if (delta?.content) {
          assistantText += delta.content;
          await writer.write(sseData({ type: "text", content: delta.content }));
        }
        if (delta?.tool_calls) {
          for (const tc of delta.tool_calls) {
            const idx = tc.index;
            if (!pendingTcs.has(idx)) {
              pendingTcs.set(idx, { id: tc.id ?? `tc_${idx}`, name: "", args: "" });
            }
            const p = pendingTcs.get(idx)!;
            if (tc.function?.name) p.name += tc.function.name;
            if (tc.function?.arguments) p.args += tc.function.arguments;
            if (tc.id && !p.id) p.id = tc.id;
          }
        }
        if (chunk.usage?.total_tokens) {
          totalTokens = chunk.usage.total_tokens;
        }
      }

      const toolCallsList = [...pendingTcs.values()].filter((t) => t.name);

      if (toolCallsList.length === 0) {
        // Final answer — exit loop
        break;
      }

      // Push assistant message with tool calls
      loopMsgs.push({
        role: "assistant",
        content: assistantText || null,
        tool_calls: toolCallsList.map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: { name: tc.name, arguments: tc.args },
        })),
      });

      // Execute each tool
      for (const tc of toolCallsList) {
        await writer.write(
          sseData({ type: "tool_call", name: tc.name, tool_call_id: tc.id }),
        );

        const toolStart = Date.now();
        let toolContent = "";
        let citations: ToolCitation[] = [];
        let success = true;

        const tool = toolRegistry.get(tc.name);
        if (!tool) {
          toolContent = JSON.stringify({ error: `Tool "${tc.name}" not found in registry` });
          success = false;
        } else {
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(tc.args) as Record<string, unknown>;
          } catch {
            // empty args
          }

          // ---- Approval gate ------------------------------------------------
          const needsApproval =
            tool.requiresApproval === true &&
            (config.approvalMode ?? "require_user") === "require_user";

          if (needsApproval) {
            const approvalKey = await createApprovalRecord(ctx.sessionId, tc.id, ctx.userId);
            await writer.write(
              sseData({
                type: "require_approval",
                tool_call_id: tc.id,
                tool_name: tc.name,
                args_preview: sanitiseArgsPreview(args),
                approval_key: approvalKey,
                reason: tool.approvalReason ?? "此操作需要您的确认。",
              }),
            );
            const approved = await waitForApproval(approvalKey);
            await writer.write(
              sseData({ type: "approval_resolved", tool_call_id: tc.id, approved }),
            );
            if (!approved) {
              toolContent = JSON.stringify({ error: "用户拒绝了此操作。" });
              success = false;
            }
          }
          // -------------------------------------------------------------------

          if (success) {
            try {
              const raw = await tool.execute(args, ctx);
              if (typeof raw === "string") {
                toolContent = raw;
              } else {
                toolContent = raw.content;
                citations = raw.citations ?? [];
              }
            } catch (err) {
              toolContent = JSON.stringify({
                error: err instanceof Error ? err.message : String(err),
              });
              success = false;
            }
          }
        }

        const durationMs = Date.now() - toolStart;

        await writer.write(
          sseData({ type: "tool_result", name: tc.name, success, duration_ms: durationMs }),
        );

        // Emit citations
        for (const c of citations) {
          await writer.write(sseData({ type: "citation", ...c }));
        }

        // Add tool result to loop messages
        loopMsgs.push({
          role: "tool",
          tool_call_id: tc.id,
          content: toolContent,
        });
      }

      if (ctx.debugTrace) {
        await writer.write(
          sseData({ type: "trace", event: `iter_done`, payload: { iter } }),
        );
      }
    }
  } catch (err) {
    streamError = err instanceof Error ? err.message : String(err);
    console.error("[ReActLoop] error during loop:", err);
  }

  const execMs = Date.now() - startMs;
  await writer.write(
    sseData({ type: "done", tokens: totalTokens, exec_time_ms: execMs, error: streamError }),
  );

  // Fire-and-forget memory consolidation.
  // Pass the actual token count from the API (prompt_tokens + completion_tokens) so the
  // threshold check uses the real Qwen usage data instead of the char-based estimate.
  const allMessages: Message[] = [
    ...opts.history,
    { role: "user", content: opts.userMessage },
  ];
  if (coordinator.shouldRunConsolidation(allMessages, totalTokens)) {
    void coordinator.consolidateSession(ctx.userId, ctx.sessionId, allMessages);
  }

  await writer.close();
}

// ---- Helpers ----------------------------------------------------------------

function _toOpenAIParam(m: Message): OpenAI.Chat.ChatCompletionMessageParam {
  if (m.role === "tool") {
    return {
      role: "tool",
      tool_call_id: m.tool_call_id ?? "",
      content: m.content,
    };
  }
  if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
    return {
      role: "assistant",
      content: m.content || null,
      tool_calls: m.tool_calls.map((tc) => ({
        id: tc.id,
        type: "function" as const,
        function: tc.function,
      })),
    };
  }
  return { role: m.role as "user" | "assistant" | "system", content: m.content };
}

function _buildUserContent(
  opts: ReactLoopOptions,
): string | OpenAI.Chat.ChatCompletionContentPart[] {
  const attachments = opts.config.attachments;
  if (!attachments || attachments.length === 0) {
    return opts.userMessage;
  }

  const parts: OpenAI.Chat.ChatCompletionContentPart[] = [
    { type: "text", text: opts.userMessage },
  ];

  for (const att of attachments) {
    if (att.mime_type.startsWith("image/")) {
      parts.push({
        type: "image_url",
        image_url: { url: att.presigned_url },
      });
    }
  }
  return parts.length > 1 ? parts : opts.userMessage;
}
