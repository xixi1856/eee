import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getCourseIfMember } from "@/lib/course-access";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

/** Read-only: existing course chat agent session id for hydration (no agent session creation). */
export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId } = await ctx.params;
    await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);

    const row = await prisma.courseChatSession.findUnique({
      where: {
        courseId_studentId: { courseId, studentId: auth.sub },
      },
      select: { agentSessionId: true },
    });

    return jsonOk({ session_id: row?.agentSessionId ?? null });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
