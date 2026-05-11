import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated, requireAdmin } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getCourseIfMember, assertUuid } from "@/lib/course-access";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { courseId } = await ctx.params;

    const url = new URL(req.url);
    const auditStudentId = url.searchParams.get("student_id")?.trim();

    if (auditStudentId) {
      requireAdmin(auth);
      assertUuid(auditStudentId, "student_id");
      const course = await prisma.course.findFirst({
        where: { id: courseId, isDeleted: false },
      });
      if (!course) {
        throw new ApiError(404, "NOT_FOUND", "Course not found");
      }
    } else {
      await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);
      if (auth.role === UserRole.TEACHER) {
        throw new ApiError(
          403,
          "FORBIDDEN",
          "Teachers cannot list raw per-message chat logs; use course analytics",
        );
      }
    }

    const limit = Math.min(
      100,
      Math.max(1, Number.parseInt(url.searchParams.get("limit") ?? "20", 10) || 20),
    );
    const offset = Math.max(
      0,
      Number.parseInt(url.searchParams.get("offset") ?? "0", 10) || 0,
    );

    const studentFilter = auditStudentId ?? auth.sub;

    const where = {
      courseId,
      studentId: studentFilter,
      deletedAt: null,
    };

    const [logs, total] = await Promise.all([
      prisma.qaLog.findMany({
        where,
        orderBy: { createdAt: "desc" },
        take: limit,
        skip: offset,
        select: {
          id: true,
          question: true,
          answer: true,
          createdAt: true,
          hitMaterials: true,
          sessionId: true,
        },
      }),
      prisma.qaLog.count({ where }),
    ]);

    return jsonOk({
      logs: logs.map((l) => ({
        id: l.id,
        question: l.question,
        answer: l.answer,
        created_at: l.createdAt.toISOString(),
        hit_materials: l.hitMaterials,
        session_id: l.sessionId,
      })),
      total,
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
