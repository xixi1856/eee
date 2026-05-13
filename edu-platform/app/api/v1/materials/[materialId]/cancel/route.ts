import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { cancelMaterialProcessing } from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ materialId: string }> };

/** POST /api/v1/materials/:materialId/cancel
 *
 * Signals the Python RAG worker to abort processing via a Redis key, then
 * soft-deletes the material and enqueues a cleanup task.
 * Only valid for materials in UPLOADED / PARSING / PARSED / INDEXING status.
 */
export async function POST(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { materialId } = await ctx.params;
    await cancelMaterialProcessing(auth.sub, auth.role as UserRole, materialId);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
