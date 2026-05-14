import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getCourseIfMember } from "@/lib/course-access";
import { prisma } from "@/lib/db";
import { createNewCourseChatSession } from "@/lib/services/chatService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

/** List all non-deleted course chat sessions for the current user (for history hydration). */
export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId } = await ctx.params;
    await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);

    const rows = await prisma.courseChatSession.findMany({
      where: { courseId, studentId: auth.sub, deletedAt: null },
      orderBy: { createdAt: "asc" },
      select: { agentSessionId: true, createdAt: true },
    });

    return jsonOk({
      sessions: rows.map((r) => ({
        session_id: r.agentSessionId,
        created_at: r.createdAt.toISOString(),
      })),
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}

/** Create a new course chat session. */
export async function POST(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId } = await ctx.params;
    await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);

    const sessionId = await createNewCourseChatSession(courseId, auth.sub);
    return jsonOk({ session_id: sessionId });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
