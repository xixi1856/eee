import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { retryMaterialIndex } from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string; materialId: string }> };

function parseBoolFlag(v: unknown, defaultTrue: boolean): boolean {
  if (v === undefined || v === null) return defaultTrue;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v !== "string") return defaultTrue;
  const s = v.trim().toLowerCase();
  if (!s) return defaultTrue;
  if (s === "1" || s === "true" || s === "yes" || s === "on") return true;
  if (s === "0" || s === "false" || s === "no" || s === "off") return false;
  return defaultTrue;
}

export async function POST(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId, materialId } = await ctx.params;
    let textOnly = true;
    let skipKg = true;
    try {
      const body = (await _req.json()) as {
        text_only?: unknown;
        skip_kg?: unknown;
      };
      textOnly = parseBoolFlag(body?.text_only, true);
      skipKg = parseBoolFlag(body?.skip_kg, true);
    } catch {
      textOnly = true;
      skipKg = true;
    }
    await retryMaterialIndex(
      auth.sub,
      auth.role as UserRole,
      courseId,
      materialId,
      textOnly,
      skipKg,
    );
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
