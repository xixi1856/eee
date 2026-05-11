import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { getInternalApiKeyOrNull } from "@/lib/config";
import { hasCourseRagAccess } from "@/lib/course-access";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Agent HTTP passes ``user_id`` = bound ``agent_user_id``. Enrollment checks use
 * platform ``users.id`` — resolve mapping when present.
 */
async function resolvePlatformUserIdForCourseRag(
  userIdParam: string,
): Promise<string> {
  const row = await prisma.agentIdentityMapping.findUnique({
    where: { agentUserId: userIdParam },
    select: { platformUserId: true },
  });
  if (row) return row.platformUserId;
  return userIdParam;
}

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
    const courseId = req.nextUrl.searchParams.get("course_id")?.trim() ?? "";
    const userId = req.nextUrl.searchParams.get("user_id")?.trim() ?? "";
    if (!courseId || !userId) {
      throw new ApiError(400, "VALIDATION_ERROR", "course_id and user_id are required");
    }
    const platformUserId = await resolvePlatformUserIdForCourseRag(userId);
    const access = await hasCourseRagAccess(platformUserId, courseId);
    return jsonOk({ access });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
