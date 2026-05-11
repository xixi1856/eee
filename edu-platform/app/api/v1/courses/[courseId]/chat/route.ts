import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { assertUuid, getCourseIfMember } from "@/lib/course-access";
import { prisma } from "@/lib/db";
import { courseChatSseResponse } from "@/lib/services/chatService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);
    if (!auth.agent_user_id) {
      throw new ApiError(
        400,
        "AGENT_NOT_BOUND",
        "Bind an agent identity before using course chat",
      );
    }
    const body = (await req.json()) as { message?: string; lesson_id?: string };
    const message = typeof body.message === "string" ? body.message.trim() : "";
    if (!message) {
      throw new ApiError(400, "VALIDATION_ERROR", "message is required");
    }
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
    return await courseChatSseResponse({
      courseId,
      platformStudentId: auth.sub,
      agentUserId: auth.agent_user_id,
      message,
      lessonId,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
