import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

/** GDPR-style export of the authenticated user's QA rows. */
export async function GET(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const logs = await prisma.qaLog.findMany({
      where: { studentId: auth.sub, deletedAt: null },
      orderBy: { createdAt: "desc" },
      take: 10_000,
    });
    return jsonOk({ user_id: auth.sub, qa_logs: logs });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
