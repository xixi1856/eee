import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { qaCenterChatSseResponse } from "@/lib/services/chatService";
import { getAccessibleCourseIds } from "@/lib/course-access-injector";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const accessibleCourseIds = await getAccessibleCourseIds(auth.sub, auth.role as UserRole);
    const body = (await req.json()) as {
      message?: string;
      session_id?: string;
      trim_history_to?: number;
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
    const trimHistoryTo =
      typeof body.trim_history_to === "number" &&
      Number.isInteger(body.trim_history_to) &&
      body.trim_history_to >= 0
        ? body.trim_history_to
        : undefined;
    const traceId = req.headers.get("x-trace-id")?.trim() || null;
    const debugTraceRaw = req.headers.get("x-debug-trace")?.trim().toLowerCase() || "";
    const debugTrace = ["1", "true", "yes", "on"].includes(debugTraceRaw);
    return await qaCenterChatSseResponse({
      platformStudentId: auth.sub,
      userId: auth.sub,
      accessibleCourseIds,
      message,
      sessionId,
      attachments,
      traceId,
      debugTrace,
      trimHistoryTo,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
