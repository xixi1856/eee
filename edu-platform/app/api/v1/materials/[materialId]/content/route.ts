import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { UserRole } from "@prisma/client";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { openMaterialContentStream } from "@/lib/services/materialService";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ materialId: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));
    const { materialId } = await ctx.params;
    const variant =
      req.nextUrl.searchParams.get("variant") === "original"
        ? "original"
        : "inline";
    const { body, contentType, contentDisposition } =
      await openMaterialContentStream({
        userId: auth.sub,
        role: auth.role as UserRole,
        materialId,
        variant,
      });
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": contentDisposition,
        "Cache-Control": "private, max-age=120",
      },
    });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
