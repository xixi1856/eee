import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

/** Soft-delete all QA logs for the authenticated user (GDPR erasure). */
export async function DELETE(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const now = new Date();
    const r = await prisma.qaLog.updateMany({
      where: { studentId: auth.sub, deletedAt: null },
      data: { deletedAt: now },
    });
    return jsonOk({ deleted_count: r.count });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
