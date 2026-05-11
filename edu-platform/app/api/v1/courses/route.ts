import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { createCourse, listMyCourses } from "@/lib/services/courseService";
import type { CreateCourseBody } from "@/lib/dto/course.dto";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    const list = await listMyCourses(ctx.sub, ctx.role as UserRole);
    return jsonOk({ courses: list });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    const body = (await req.json()) as CreateCourseBody;
    const created = await createCourse(ctx.sub, ctx.role as UserRole, body);
    return jsonOk(created, 201);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
