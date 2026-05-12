import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { getInternalApiKeyOrNull } from "@/lib/config";
import { hasCourseRagAccess } from "@/lib/course-access";
import { resolvePlatformUserFromAgentQuery } from "@/lib/internal-agent-user";
import { prisma } from "@/lib/db";

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
    if (!access) {
      throw new ApiError(403, "FORBIDDEN", "No course access");
    }

    const materials = await prisma.material.findMany({
      where: {
        courseId,
        isDeleted: false,
        status: "READY",
      },
      orderBy: [{ updatedAt: "desc" }],
      select: {
        id: true,
        originalFilename: true,
      },
    });

    return jsonOk({
      materials: materials.map((m) => ({
        id: m.id,
        original_filename: m.originalFilename,
      })),
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
