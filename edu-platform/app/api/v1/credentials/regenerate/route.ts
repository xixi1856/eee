import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { regenerateCredentialForSelf } from "@/lib/services/credentialService";
import { UserRole } from "@prisma/client";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    // Only students and teachers can self-regenerate; admins use the admin endpoint
    if (ctx.role === UserRole.ADMIN) {
      throw new ApiError(
        403,
        "FORBIDDEN",
        "Admins generate credentials via /api/v1/admin/credentials only",
      );
    }
    if (ctx.role !== UserRole.STUDENT && ctx.role !== UserRole.TEACHER) {
      throw new ApiError(403, "FORBIDDEN", "Credential regeneration not allowed for this role");
    }
    const result = await regenerateCredentialForSelf(ctx.sub);
    return NextResponse.json({ code: result.code, expires_at: result.expires_at });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Internal server error"));
  }
}
