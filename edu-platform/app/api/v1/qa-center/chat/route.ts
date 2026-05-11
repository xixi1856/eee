import type { NextRequest } from "next/server";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { agentNotBoundError } from "@/lib/agent-not-bound-error";
import { qaCenterChatSseResponse } from "@/lib/services/chatService";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    if (!auth.agent_user_id) {
      throw agentNotBoundError();
    }
    const body = (await req.json()) as {
      message?: string;
      session_id?: string;
      attachments?: {
        id: string;
        key: string;
        presigned_url: string;
        mime_type: string;
        name: string;
      }[];
    };
    const message = typeof body.message === "string" ? body.message.trim() : "";
    if (!message && (!Array.isArray(body.attachments) || body.attachments.length === 0)) {
      throw new ApiError(400, "VALIDATION_ERROR", "message or attachments is required");
    }
    const rawAttachments = Array.isArray(body.attachments) ? body.attachments.slice(0, 10) : [];
    const attachments = rawAttachments
      .filter((a) => a && typeof a.id === "string" && typeof a.presigned_url === "string")
      .map(({ id, key, presigned_url, mime_type, name }) => ({
        id,
        key,
        presigned_url,
        mime_type,
        name,
      }));
    const sessionId =
      typeof body.session_id === "string" && body.session_id.trim()
        ? body.session_id.trim()
        : null;
    return await qaCenterChatSseResponse({
      platformStudentId: auth.sub,
      agentUserId: auth.agent_user_id,
      message,
      sessionId,
      attachments,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
