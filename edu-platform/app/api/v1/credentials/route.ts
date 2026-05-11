import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { listMyCredentials } from "@/lib/services/credentialService";
import { UserRole } from "@prisma/client";

export const dynamic = "force-dynamic";

function assertCredentialAccess(role: UserRole): void {
  if (role === UserRole.ADMIN) {
    throw new ApiError(
      403,
      "FORBIDDEN",
      "Admins manage credentials via /api/v1/admin/credentials only",
    );
  }
  if (role !== UserRole.STUDENT && role !== UserRole.TEACHER) {
    throw new ApiError(403, "FORBIDDEN", "Credential access denied");
  }
}

export async function GET(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    assertCredentialAccess(ctx.role as UserRole);
    const list = await listMyCredentials(ctx.sub);
    return jsonOk({ credentials: list });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST() {
  return jsonError(
    new ApiError(
      403,
      "FORBIDDEN",
      "Self-service credential creation is disabled; codes are issued at registration (students and teachers) or by an administrator",
    ),
  );
}
