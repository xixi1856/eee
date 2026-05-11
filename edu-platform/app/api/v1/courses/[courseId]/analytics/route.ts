import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { assertTeacherOfCourse } from "@/lib/course-access";
import { prisma } from "@/lib/db";
import { getCourseAnalytics } from "@/lib/services/analyticsService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;
    if (auth.role === UserRole.ADMIN) {
      const c = await prisma.course.findFirst({
        where: { id: courseId, isDeleted: false },
      });
      if (!c) {
        throw new ApiError(404, "NOT_FOUND", "Course not found");
      }
    } else {
      await assertTeacherOfCourse(auth.sub, auth.role as UserRole, courseId);
    }
    const url = new URL(req.url);
    const start = url.searchParams.get("start_date");
    const end = url.searchParams.get("end_date");
    const data = await getCourseAnalytics(courseId, start, end);
    return jsonOk(data);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
