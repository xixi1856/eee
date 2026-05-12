import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { retryMaterialIndex } from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string; materialId: string }> };

function parseTextOnly(v: unknown): boolean {
  if (typeof v !== "string") return true;
  const s = v.trim().toLowerCase();
  if (!s) return true;
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

export async function POST(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId, materialId } = await ctx.params;
    let textOnly = true;
    try {
      const body = (await _req.json()) as { text_only?: unknown };
      textOnly = parseTextOnly(body?.text_only);
    } catch {
      textOnly = true;
    }
    await retryMaterialIndex(
      auth.sub,
      auth.role as UserRole,
      courseId,
      materialId,
      textOnly,
    );
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
