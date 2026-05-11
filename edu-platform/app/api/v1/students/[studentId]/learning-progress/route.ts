import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated, requireAdmin } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getStudentLearningProgress } from "@/lib/services/analyticsService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ studentId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { studentId } = await ctx.params;
    if (auth.sub !== studentId) {
      if (auth.role !== UserRole.ADMIN) {
        throw new ApiError(
          403,
          "FORBIDDEN",
          "You may only access your own learning progress",
        );
      }
      requireAdmin(auth);
    }
    const data = await getStudentLearningProgress(studentId);
    return jsonOk(data);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
