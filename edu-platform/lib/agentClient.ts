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
  courseId: string;
  lessonId?: string | null;
  sessionId: string;
  userMessage: string;
  stream: boolean;
  attachments?: AgentAttachment[];
};

function agentHeaders(agentUserId: string, courseId: string, lessonId?: string | null): Headers {
  const h = new Headers();
  h.set("Content-Type", "application/json");
  const key = getEduAgentApiKey();
  if (key) {
    h.set("Authorization", `Bearer ${key}`);
  }
  h.set("X-Platform-User-Id", agentUserId);
  h.set("X-Platform-Course-Id", courseId);
  if (lessonId) {
    h.set("X-Platform-Lesson-Id", lessonId);
  }
  return h;
}

/** POST /v1/sessions on EduAgent (runtime: platform issues agent user id). */
export async function createAgentSession(agentUserId: string, title?: string): Promise<string> {
  const base = getEduAgentBaseUrl();
  if (!base) {
    throw new ApiError(503, "AGENT_UNAVAILABLE", "EDU_AGENT_BASE_URL is not configured");
  }
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  const key = getEduAgentApiKey();
  if (key) {
    headers.set("Authorization", `Bearer ${key}`);
  }
  const res = await fetch(`${base}/v1/sessions`, {
    method: "POST",
    headers,
    body: JSON.stringify({ user_id: agentUserId, title: title ?? null }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new ApiError(
      502,
      "AGENT_SESSION_CREATE_FAILED",
      `Agent session create failed: ${res.status} ${t.slice(0, 400)}`,
    );
  }
  const j = (await res.json()) as { id?: string };
  if (!j.id) {
    throw new ApiError(502, "AGENT_SESSION_CREATE_FAILED", "Agent returned no session id");
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
    throw new ApiError(503, "AGENT_UNAVAILABLE", "EDU_AGENT_BASE_URL is not configured");
  }
  const q = new URLSearchParams({
    session_id: req.sessionId,
    user_id: req.agentUserId,
  });
  const url = `${base}/v1/chat/completions?${q.toString()}`;
  const res = await fetch(url, {
    method: "POST",
    headers: agentHeaders(req.agentUserId, req.courseId, req.lessonId ?? undefined),
    body: JSON.stringify({
      model: "",
      messages: [{ role: "user", content: req.userMessage }],
      stream: req.stream,
      ...(req.attachments?.length ? { attachments: req.attachments } : {}),
    }),
  });
  return res;
}
