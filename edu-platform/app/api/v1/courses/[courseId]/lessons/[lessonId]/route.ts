import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { deleteLesson, updateLesson } from "@/lib/services/courseService";
import type { UpdateLessonBody } from "@/lib/dto/course.dto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string; lessonId: string }> };

export async function PATCH(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId, lessonId } = await ctx.params;
    const body = (await req.json()) as UpdateLessonBody;
    const lesson = await updateLesson(
      auth.sub,
      auth.role as UserRole,
      courseId,
      lessonId,
      body,
    );
    return jsonOk(lesson);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function DELETE(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId, lessonId } = await ctx.params;
    await deleteLesson(auth.sub, auth.role as UserRole, courseId, lessonId);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
