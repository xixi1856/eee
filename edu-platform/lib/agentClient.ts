import { getEduAgentApiKey, getEduAgentBaseUrl } from "@/lib/config";
import { ApiError } from "@/lib/http/api-error";

export type AgentAttachment = {
  id: string;
  presigned_url: string;
  mime_type: string;
  name: string;
};

export type AgentChatRequest = {
  agentUserId: string;
  /** When omitted, EduAgent has no course scope (QA center cross-course RAG). */
  courseId?: string | null;
  lessonId?: string | null;
  sessionId: string;
  userMessage: string;
  stream: boolean;
  attachments?: AgentAttachment[];
};

const MSG_AGENT_BASE_MISSING =
  "未配置 EduAgent 网关地址：请在 edu-platform 的环境变量中设置 EDU_AGENT_BASE_URL（无末尾斜杠，例如 http://127.0.0.1:8765），与 edu-gateway 监听地址一致。";

const MSG_AGENT_GATEWAY_UNREACHABLE =
  "无法连接 EduAgent 网关（edu-gateway 可能未启动或地址不对）。请在仓库根目录运行 uv run edu-gateway（默认可配合 --host 127.0.0.1 --port 8765），并确认 EDU_AGENT_BASE_URL 与该地址一致。";

function flattenErrorText(err: unknown): string {
  if (!(err instanceof Error)) return String(err);
  const parts = [err.message];
  if (err.cause instanceof Error) {
    parts.push(err.cause.message);
  }
  return parts.join(" ");
}

/** Heuristic for undici/Node fetch TCP/DNS failures when edu-gateway is down or URL wrong. */
function isLikelyAgentConnectionFailure(err: unknown): boolean {
  const t = flattenErrorText(err);
  return /ECONNREFUSED|ENOTFOUND|ETIMEDOUT|EAI_AGAIN|ECONNRESET|fetch failed|socket|network/i.test(
    t,
  );
}

function agentUnavailable(
  reason: "missing_base_url" | "gateway_unreachable",
): ApiError {
  const message =
    reason === "missing_base_url" ? MSG_AGENT_BASE_MISSING : MSG_AGENT_GATEWAY_UNREACHABLE;
  return new ApiError(503, "AGENT_UNAVAILABLE", message, { reason });
}

function agentHeaders(
  agentUserId: string,
  courseId: string | null | undefined,
  lessonId?: string | null,
): Headers {
  const h = new Headers();
  h.set("Content-Type", "application/json");
  const key = getEduAgentApiKey();
  if (key) {
    h.set("Authorization", `Bearer ${key}`);
  }
  h.set("X-Platform-User-Id", agentUserId);
  if (courseId) {
    h.set("X-Platform-Course-Id", courseId);
  }
  if (lessonId) {
    h.set("X-Platform-Lesson-Id", lessonId);
  }
  return h;
}

/** POST /v1/sessions on EduAgent (runtime: platform issues agent user id). */
export async function createAgentSession(agentUserId: string, title?: string): Promise<string> {
  const base = getEduAgentBaseUrl();
  if (!base) {
    throw agentUnavailable("missing_base_url");
  }
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  const key = getEduAgentApiKey();
  if (key) {
    headers.set("Authorization", `Bearer ${key}`);
  }
  let res: Response;
  try {
    res = await fetch(`${base}/v1/sessions`, {
      method: "POST",
      headers,
      body: JSON.stringify({ user_id: agentUserId, title: title ?? null }),
    });
  } catch (e) {
    if (isLikelyAgentConnectionFailure(e)) {
      throw agentUnavailable("gateway_unreachable");
    }
    throw e;
  }
  if (!res.ok) {
    const t = await res.text();
    throw new ApiError(
      502,
      "AGENT_SESSION_CREATE_FAILED",
      `EduAgent 创建会话失败：HTTP ${res.status} ${t.slice(0, 400)}`,
    );
  }
  const j = (await res.json()) as { id?: string };
  if (!j.id) {
    throw new ApiError(502, "AGENT_SESSION_CREATE_FAILED", "EduAgent 未返回会话 id");
  }
  return j.id;
}

/**
 * Stream POST /v1/chat/completions. Caller must consume body or cancel.
 * Runtime context: query ``user_id`` = agent user id; headers carry course/lesson for RAG.
 */
export async function postChatCompletionsStream(
  req: AgentChatRequest,
): Promise<Response> {
  const base = getEduAgentBaseUrl();
  if (!base) {
    throw agentUnavailable("missing_base_url");
  }
  const q = new URLSearchParams({
    session_id: req.sessionId,
    user_id: req.agentUserId,
  });
  const url = `${base}/v1/chat/completions?${q.toString()}`;
  try {
    return await fetch(url, {
      method: "POST",
      headers: agentHeaders(req.agentUserId, req.courseId ?? null, req.lessonId ?? undefined),
      body: JSON.stringify({
        model: "",
        messages: [{ role: "user", content: req.userMessage }],
        stream: req.stream,
        ...(req.attachments?.length ? { attachments: req.attachments } : {}),
      }),
    });
  } catch (e) {
    if (isLikelyAgentConnectionFailure(e)) {
      throw agentUnavailable("gateway_unreachable");
    }
    throw e;
  }
}
