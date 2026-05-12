import { prisma } from "@/lib/db";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const px = prisma as any;
import { createAgentSession, postChatCompletionsStream } from "@/lib/agentClient";
import { ApiError } from "@/lib/http/api-error";

export type B3SseEvent =
  | { type: "text"; content: string }
  | {
      type: "citation";
      chunk_id?: string;
      material_id?: string;
      source_label?: string;
    }
  | {
      type: "tool_call";
      name: string;
      tool_call_id?: string;
    }
  | {
      type: "tool_result";
      name: string;
      success?: boolean;
      duration_ms?: number;
    }
  | {
      type: "done";
      tokens?: number | null;
      exec_time_ms?: number | null;
      error?: string;
    };

const enc = new TextEncoder();

function sseDataLine(obj: B3SseEvent): Uint8Array {
  return enc.encode(`data: ${JSON.stringify(obj)}\n\n`);
}

function parseSseDataPayload(block: string): string | null {
  const lines = block.split("\n").map((l) => l.replace(/\r$/, ""));
  const dataLines = lines.filter((l) => l.startsWith("data:"));
  if (dataLines.length === 0) return null;
  return dataLines.map((l) => l.replace(/^data:\s?/, "")).join("\n");
}

function citationsFromB3(b3: Record<string, unknown>): B3SseEvent[] {
  const chunks = Array.isArray(b3.hit_chunks) ? (b3.hit_chunks as string[]) : [];
  const mats = Array.isArray(b3.hit_materials) ? (b3.hit_materials as string[]) : [];
  const srcs = Array.isArray(b3.hit_sources) ? (b3.hit_sources as string[]) : [];
  const n = Math.max(chunks.length, mats.length, srcs.length);
  const out: B3SseEvent[] = [];
  for (let i = 0; i < n; i++) {
    out.push({
      type: "citation",
      chunk_id: chunks[i] || undefined,
      material_id: mats[i] || undefined,
      source_label: srcs[i] || undefined,
    });
  }
  return out;
}

function toolCallEventFromAgentFrame(
  frame: Record<string, unknown>,
): Extract<B3SseEvent, { type: "tool_call" }> | null {
  const eduMeta = frame.edu_meta as Record<string, unknown> | undefined;
  if (eduMeta?.content_type !== "tool_call") return null;
  const choices = frame.choices as Record<string, unknown>[] | undefined;
  const ch0 = choices?.[0] as Record<string, unknown> | undefined;
  const delta = ch0?.delta as Record<string, unknown> | undefined;
  const toolCalls = delta?.tool_calls as unknown[] | undefined;
  const first = toolCalls?.[0] as Record<string, unknown> | undefined;
  const fn = first?.function as Record<string, unknown> | undefined;
  const name = typeof fn?.name === "string" ? fn.name.trim() : "";
  if (!name) return null;
  const tool_call_id = typeof first?.id === "string" ? first.id : undefined;
  return { type: "tool_call", name, tool_call_id };
}

function toolResultEventFromAgentFrame(
  frame: Record<string, unknown>,
): Extract<B3SseEvent, { type: "tool_result" }> | null {
  const eduMeta = frame.edu_meta as Record<string, unknown> | undefined;
  if (eduMeta?.content_type !== "tool_result") return null;
  const b3 = eduMeta.b3 as Record<string, unknown> | undefined;
  if (!b3 || typeof b3 !== "object") return null;
  const name = typeof b3.tool_name === "string" ? b3.tool_name.trim() : "";
  if (!name) return null;
  const success = typeof b3.success === "boolean" ? b3.success : undefined;
  const ds = b3.duration_s;
  const duration_ms =
    typeof ds === "number" && Number.isFinite(ds) ? Math.round(ds * 1000) : undefined;
  return { type: "tool_result", name, success, duration_ms };
}

export async function getOrCreateCourseChatSession(
  courseId: string,
  platformStudentId: string,
  agentUserId: string,
): Promise<string> {
  const row = await prisma.courseChatSession.findUnique({
    where: {
      courseId_studentId: { courseId, studentId: platformStudentId },
    },
  });
  if (row) return row.agentSessionId;
  const agentSessionId = await createAgentSession(
    agentUserId,
    `course:${courseId}`,
  );
  try {
    await prisma.courseChatSession.create({
      data: {
        courseId,
        studentId: platformStudentId,
        agentSessionId,
      },
    });
  } catch {
    // concurrent create — fetch again
    const again = await prisma.courseChatSession.findUnique({
      where: {
        courseId_studentId: { courseId, studentId: platformStudentId },
      },
    });
    if (again) return again.agentSessionId;
    throw new ApiError(500, "INTERNAL_ERROR", "Failed to persist chat session");
  }
  return agentSessionId;
}

type PersistArgs = {
  courseId: string | null;
  platformStudentId: string;
  lessonId: string | null;
  sessionId: string;
  question: string;
  answer: string;
  persist: boolean;
  b3?: Record<string, unknown>;
};

async function maybePersistQaLog(a: PersistArgs): Promise<void> {
  if (!a.persist || !a.b3) return;
  const b3 = a.b3;
  const exec = Number(b3.execution_time_ms);
  const modelUsed = typeof b3.model_used === "string" ? b3.model_used : "unknown";
  await prisma.qaLog.create({
    data: {
      courseId: a.courseId,
      studentId: a.platformStudentId,
      lessonId: a.lessonId,
      sessionId: a.sessionId,
      question: a.question,
      questionTokens:
        typeof b3.prompt_tokens === "number" ? b3.prompt_tokens : null,
      answer: a.answer,
      answerTokens:
        typeof b3.completion_tokens === "number" ? b3.completion_tokens : null,
      totalTokens:
        typeof b3.total_tokens === "number" ? b3.total_tokens : null,
      executionTimeMs: Number.isFinite(exec) ? exec : 0,
      modelUsed: modelUsed.slice(0, 100),
      hitChunks: Array.isArray(b3.hit_chunks) ? (b3.hit_chunks as string[]) : [],
      hitMaterials: Array.isArray(b3.hit_materials)
        ? (b3.hit_materials as string[])
        : [],
      hitSources: Array.isArray(b3.hit_sources) ? (b3.hit_sources as string[]) : [],
    } as Parameters<typeof prisma.qaLog.create>[0]["data"],
  });
}

/**
 * Transform Agent OpenAI-style SSE into B3 ``text`` / ``citation`` / ``done`` events.
 * After the stream completes, optionally inserts ``QaLog``.
 */
export function createB3SseTransformFromAgent(
  persistCtx: PersistArgs,
): TransformStream<Uint8Array, Uint8Array> {
  let buf = "";
  let fullAnswer = "";
  let lastB3: Record<string, unknown> | undefined;
  let sawDoneMarker = false;
  let streamError: string | undefined;

  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      buf += new TextDecoder().decode(chunk, { stream: true });
      for (;;) {
        const idx = buf.indexOf("\n\n");
        if (idx === -1) break;
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const raw = parseSseDataPayload(block);
        if (raw === null) continue;
        if (raw === "[DONE]") {
          sawDoneMarker = true;
          for (const c of citationsFromB3(lastB3 ?? {})) {
            controller.enqueue(sseDataLine(c));
          }
          const tokens =
            typeof lastB3?.total_tokens === "number"
              ? lastB3.total_tokens
              : null;
          const exec =
            typeof lastB3?.execution_time_ms === "number"
              ? lastB3.execution_time_ms
              : null;
          controller.enqueue(
            sseDataLine({
              type: "done",
              tokens,
              exec_time_ms: exec,
              error: streamError,
            }),
          );
          continue;
        }
        let frame: Record<string, unknown>;
        try {
          frame = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          streamError = streamError ?? "INVALID_AGENT_SSE_JSON";
          continue;
        }
        if (frame.error && !Array.isArray(frame.choices)) {
          streamError =
            typeof frame.error === "string"
              ? frame.error
              : JSON.stringify(frame.error);
          continue;
        }
        const choices = frame.choices as Record<string, unknown>[] | undefined;
        const ch0 = choices?.[0] as Record<string, unknown> | undefined;
        const delta = ch0?.delta as Record<string, unknown> | undefined;
        const content =
          delta && typeof delta.content === "string" ? delta.content : "";
        if (content) {
          fullAnswer += content;
          controller.enqueue(sseDataLine({ type: "text", content }));
        }
        const tcEv = toolCallEventFromAgentFrame(frame);
        if (tcEv) controller.enqueue(sseDataLine(tcEv));
        const trEv = toolResultEventFromAgentFrame(frame);
        if (trEv) controller.enqueue(sseDataLine(trEv));
        const eduMeta = frame.edu_meta as Record<string, unknown> | undefined;
        const b3 = eduMeta?.b3 as Record<string, unknown> | undefined;
        if (b3 && typeof b3 === "object") {
          lastB3 = { ...lastB3, ...b3 };
        }
      }
    },
    async flush(controller) {
      if (!sawDoneMarker) {
        controller.enqueue(
          sseDataLine({
            type: "done",
            tokens: null,
            exec_time_ms: null,
            error: streamError ?? "STREAM_INCOMPLETE",
          }),
        );
        return;
      }
      if (!streamError) {
        await maybePersistQaLog({
          ...persistCtx,
          answer: fullAnswer,
          b3: lastB3,
        });
      }
    },
  });
}

export type AttachmentParam = {
  id: string;
  key: string;
  presigned_url: string;
  mime_type: string;
  name: string;
};

export type CourseChatParams = {
  courseId: string;
  platformStudentId: string;
  agentUserId: string;
  message: string;
  lessonId?: string | null;
  attachments?: AttachmentParam[];
};

/**
 * Returns a ``Response`` with ``text/event-stream`` body (B3 SSE), or throws ``ApiError``.
 */
export async function courseChatSseResponse(
  p: CourseChatParams,
): Promise<Response> {
  const user = await prisma.user.findFirst({
    where: { id: p.platformStudentId, isActive: true },
    select: { qaCollectionEnabled: true },
  });
  if (!user) {
    throw new ApiError(404, "NOT_FOUND", "User not found");
  }
  const sessionId = await getOrCreateCourseChatSession(
    p.courseId,
    p.platformStudentId,
    p.agentUserId,
  );
  const agentRes = await postChatCompletionsStream({
    agentUserId: p.agentUserId,
    courseId: p.courseId,
    lessonId: p.lessonId ?? null,
    sessionId,
    userMessage: p.message,
    stream: true,
    attachments: p.attachments,
  });
  if (!agentRes.ok) {
    const t = await agentRes.text();
    throw new ApiError(
      502,
      "AGENT_CHAT_FAILED",
      `Agent chat failed: ${agentRes.status} ${t.slice(0, 400)}`,
    );
  }
  if (!agentRes.body) {
    throw new ApiError(502, "AGENT_CHAT_FAILED", "Agent returned empty body");
  }
  const persist = user.qaCollectionEnabled;
  const transform = createB3SseTransformFromAgent({
    courseId: p.courseId,
    platformStudentId: p.platformStudentId,
    lessonId: p.lessonId ?? null,
    sessionId,
    question: p.message,
    answer: "",
    persist,
  });
  const out = agentRes.body.pipeThrough(transform);
  return new Response(out, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}

export type QaCenterChatParams = {
  platformStudentId: string;
  agentUserId: string;
  message: string;
  sessionId?: string | null;
  attachments?: AttachmentParam[];
};

async function getOrCreateQaCenterAgentSession(
  platformStudentId: string,
  agentUserId: string,
  existingSessionId?: string | null,
): Promise<string> {
  const sid = existingSessionId?.trim();
  if (sid) {
    const row = await px.qaCenterSession.findFirst({
      where: {
        agentSessionId: sid,
        studentId: platformStudentId,
        deletedAt: null,
      },
    });
    if (!row) {
      throw new ApiError(404, "NOT_FOUND", "问答会话不存在或已删除");
    }
    return row.agentSessionId;
  }
  const agentSessionId = await createAgentSession(agentUserId, "问答中心");
  try {
    await px.qaCenterSession.create({
      data: {
        studentId: platformStudentId,
        agentSessionId,
      },
    });
  } catch {
    const again = await px.qaCenterSession.findFirst({
      where: {
        agentSessionId,
        studentId: platformStudentId,
        deletedAt: null,
      },
    });
    if (again) return again.agentSessionId;
    throw new ApiError(500, "INTERNAL_ERROR", "Failed to persist QA center session");
  }
  return agentSessionId;
}

/** QA center: no course header; RAG uses ``enrolled_courses`` in EduAgent. */
export async function qaCenterChatSseResponse(
  p: QaCenterChatParams,
): Promise<Response> {
  const user = await prisma.user.findFirst({
    where: { id: p.platformStudentId, isActive: true },
    select: { qaCollectionEnabled: true },
  });
  if (!user) {
    throw new ApiError(404, "NOT_FOUND", "User not found");
  }
  const sessionId = await getOrCreateQaCenterAgentSession(
    p.platformStudentId,
    p.agentUserId,
    p.sessionId,
  );
  const agentRes = await postChatCompletionsStream({
    agentUserId: p.agentUserId,
    courseId: null,
    lessonId: null,
    sessionId,
    userMessage: p.message,
    stream: true,
    attachments: p.attachments,
  });
  if (!agentRes.ok) {
    const t = await agentRes.text();
    throw new ApiError(
      502,
      "AGENT_CHAT_FAILED",
      `Agent chat failed: ${agentRes.status} ${t.slice(0, 400)}`,
    );
  }
  if (!agentRes.body) {
    throw new ApiError(502, "AGENT_CHAT_FAILED", "Agent returned empty body");
  }
  const persist = user.qaCollectionEnabled;
  const transform = createB3SseTransformFromAgent({
    courseId: null,
    platformStudentId: p.platformStudentId,
    lessonId: null,
    sessionId,
    question: p.message,
    answer: "",
    persist,
  });
  const out = agentRes.body.pipeThrough(transform);
  return new Response(out, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Qa-Center-Session-Id": sessionId,
    },
  });
}
