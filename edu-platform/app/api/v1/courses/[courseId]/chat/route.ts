import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { assertUuid, getCourseIfMember } from "@/lib/course-access";
import { prisma } from "@/lib/db";
import { courseChatSseResponse } from "@/lib/services/chatService";
import { getAccessibleCourseIds } from "@/lib/course-access-injector";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);
    const accessibleCourseIds = await getAccessibleCourseIds(auth.sub, auth.role as UserRole);
    const body = (await req.json()) as {
      message?: string;
      lesson_id?: string;
      session_id?: string;
      trim_history_to?: number;
      attachments?: { id: string; key: string; presigned_url: string; mime_type: string; name: string }[];
    };
    const message = typeof body.message === "string" ? body.message.trim() : "";
    if (!message && (!Array.isArray(body.attachments) || body.attachments.length === 0)) {
      throw new ApiError(400, "VALIDATION_ERROR", "message or attachments is required");
    }
    const rawAttachments = Array.isArray(body.attachments) ? body.attachments.slice(0, 10) : [];
    const attachments = rawAttachments
      .filter((a) => a && typeof a.id === "string" && typeof a.presigned_url === "string")
      .map(({ id, key, presigned_url, mime_type, name }) => ({ id, key, presigned_url, mime_type, name }));
    const trimHistoryTo =
      typeof body.trim_history_to === "number" &&
      Number.isInteger(body.trim_history_to) &&
      body.trim_history_to >= 0
        ? body.trim_history_to
        : undefined;
    let lessonId: string | null = null;
    if (body.lesson_id) {
      assertUuid(body.lesson_id, "lesson_id");
      const le = await prisma.lesson.findFirst({
        where: { id: body.lesson_id, courseId, isDeleted: false },
      });
      if (!le) {
        throw new ApiError(404, "NOT_FOUND", "Lesson not found in this course");
      }
      lessonId = le.id;
    }
    const traceId = req.headers.get("x-trace-id")?.trim() || null;
    const debugTraceRaw = req.headers.get("x-debug-trace")?.trim().toLowerCase() || "";
    const debugTrace = ["1", "true", "yes", "on"].includes(debugTraceRaw);
    let sessionId: string | null = null;
    if (typeof body.session_id === "string" && body.session_id.trim()) {
      assertUuid(body.session_id, "session_id");
      sessionId = body.session_id.trim();
    }
    return await courseChatSseResponse({
      courseId,
      platformStudentId: auth.sub,
      userId: auth.sub,
      accessibleCourseIds,
      message,
      lessonId,
      attachments,
      traceId,
      debugTrace,
      trimHistoryTo,
      sessionId,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
