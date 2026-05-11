import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { getInternalApiKeyOrNull } from "@/lib/config";
import { resolvePlatformUserFromAgentQuery } from "@/lib/internal-agent-user";
import { listCourseIdsForRag } from "@/lib/course-rag-courses";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * List course UUIDs the agent user may RAG over (enrollments + teaching courses).
 * Query ``user_id`` = Agent bound id; resolved to platform user when mapped.
 */
export async function GET(req: NextRequest) {
  try {
    const expected = getInternalApiKeyOrNull();
    if (!expected) {
      throw new ApiError(
        503,
        "SERVICE_UNAVAILABLE",
        "INTERNAL_API_KEY is not configured",
      );
    }
    const key = req.headers.get("x-internal-key");
    if (key !== expected) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid internal key");
    }
    const userId = req.nextUrl.searchParams.get("user_id")?.trim() ?? "";
    if (!userId) {
      throw new ApiError(400, "VALIDATION_ERROR", "user_id is required");
    }
    const platformUserId = await resolvePlatformUserFromAgentQuery(userId);
    const user = await prisma.user.findFirst({
      where: { id: platformUserId, isActive: true },
      select: { role: true },
    });
    if (!user) {
      return jsonOk({ course_ids: [] as string[] });
    }
    const courseIds = await listCourseIdsForRag(platformUserId, user.role);
    return jsonOk({ course_ids: courseIds });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
