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
    const range = req.headers.get("range") ?? undefined;
    const { body, contentType, contentDisposition, contentLength, contentRange, isPartial } =
      await openMaterialContentStream({
        userId: auth.sub,
        role: auth.role as UserRole,
        materialId,
        variant,
        range,
      });
    const status = isPartial ? 206 : 200;
    const headers: Record<string, string> = {
      "Content-Type": contentType,
      "Content-Disposition": contentDisposition,
      "Cache-Control": "private, max-age=120",
      "Accept-Ranges": "bytes",
    };
    if (typeof contentLength === "number") {
      headers["Content-Length"] = String(contentLength);
    }
    if (contentRange) {
      headers["Content-Range"] = contentRange;
    }
    return new NextResponse(body, { status, headers });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
