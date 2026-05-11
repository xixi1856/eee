import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { getInternalApiKeyOrNull } from "@/lib/config";
import { hasCourseRagAccess } from "@/lib/course-access";
import { resolvePlatformUserFromAgentQuery } from "@/lib/internal-agent-user";

export const dynamic = "force-dynamic";

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
    const platformUserId = await resolvePlatformUserFromAgentQuery(userId);
    const access = await hasCourseRagAccess(platformUserId, courseId);
    return jsonOk({ access });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
