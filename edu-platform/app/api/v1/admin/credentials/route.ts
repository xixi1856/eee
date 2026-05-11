import type { NextRequest } from "next/server";
import { CredentialStatus, UserRole } from "@prisma/client";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAdmin, requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import {
  createAdminCredentialForUser,
  listAdminCredentials,
} from "@/lib/services/credentialService";

export const dynamic = "force-dynamic";

function parseStatus(s: string | null): CredentialStatus | undefined {
  if (!s) return undefined;
  if (
    s === "ACTIVE" ||
    s === "USED" ||
    s === "EXPIRED" ||
    s === "REVOKED"
  ) {
    return s as CredentialStatus;
  }
  throw new ApiError(400, "VALIDATION_ERROR", "Invalid status filter");
}

export async function GET(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    requireAdmin(ctx);
    const { searchParams } = new URL(req.url);
    const user_id = searchParams.get("user_id") ?? undefined;
    const statusRaw = searchParams.get("status");
    const status = statusRaw ? parseStatus(statusRaw) : undefined;
    const list = await listAdminCredentials(ctx.role as UserRole, {
      user_id: user_id ?? undefined,
      status,
    });
    return jsonOk({ credentials: list });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    requireAdmin(ctx);
    const body = (await req.json()) as {
      user_id?: string;
      expires_in_minutes?: number;
    };
    if (typeof body.user_id !== "string") {
      throw new ApiError(400, "VALIDATION_ERROR", "user_id required");
    }
    let expires: number | undefined;
    if (body.expires_in_minutes !== undefined) {
      if (
        typeof body.expires_in_minutes !== "number" ||
        !Number.isFinite(body.expires_in_minutes)
      ) {
        throw new ApiError(400, "VALIDATION_ERROR", "expires_in_minutes invalid");
      }
      expires = Math.floor(body.expires_in_minutes);
    }
    const created = await createAdminCredentialForUser(
      ctx.role as UserRole,
      body.user_id,
      expires,
    );
    return jsonOk(created);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
