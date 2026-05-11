import type { NextRequest } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { assertMaterialReadAccess } from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ materialId: string; chunkId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { materialId } = await ctx.params;
    await assertMaterialReadAccess(auth.sub, auth.role as UserRole, materialId);
    return jsonError(
      new ApiError(
        501,
        "NOT_IMPLEMENTED",
        "Chunk text API is not implemented; use citation context or material preview.",
      ),
    );
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
