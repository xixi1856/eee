import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { createLesson, listLessons, reorderLessons } from "@/lib/services/courseService";
import type { CreateLessonBody, ReorderLessonsBody } from "@/lib/dto/course.dto";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const lessons = await listLessons(auth.sub, auth.role as UserRole, courseId);
    return jsonOk({ lessons });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const body = (await req.json()) as CreateLessonBody;
    const lesson = await createLesson(auth.sub, auth.role as UserRole, courseId, body);
    return jsonOk(lesson, 201);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function PUT(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    const body = (await req.json()) as ReorderLessonsBody;
    await reorderLessons(auth.sub, auth.role as UserRole, courseId, body);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
