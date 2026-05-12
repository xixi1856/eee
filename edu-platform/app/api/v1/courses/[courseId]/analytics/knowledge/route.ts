import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { assertTeacherOfCourse } from "@/lib/course-access";
import { prisma } from "@/lib/db";
import {
  getKnowledgeAnalytics,
  type KnowledgeAnalyticsRange,
} from "@/lib/services/analyticsService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

const VALID_RANGES = new Set<KnowledgeAnalyticsRange>(["7d", "30d", "all"]);

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;

    if (auth.role === UserRole.ADMIN) {
      const c = await prisma.course.findFirst({
        where: { id: courseId, isDeleted: false },
      });
      if (!c) throw new ApiError(404, "NOT_FOUND", "Course not found");
    } else {
      await assertTeacherOfCourse(auth.sub, auth.role as UserRole, courseId);
    }

    const url = new URL(req.url);
    const rawRange = url.searchParams.get("range") ?? "7d";
    const range: KnowledgeAnalyticsRange = VALID_RANGES.has(
      rawRange as KnowledgeAnalyticsRange,
    )
      ? (rawRange as KnowledgeAnalyticsRange)
      : "7d";

    const data = await getKnowledgeAnalytics(courseId, range);
    return jsonOk(data);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
