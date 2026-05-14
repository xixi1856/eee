import type { NextRequest } from "next/server";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getRedis } from "@/lib/redis";
import { ApiError } from "@/lib/http/api-error";
import { jsonError, jsonOk } from "@/lib/http/json-response";

export const dynamic = "force-dynamic";

/**
 * POST /api/v1/chat/approval
 *
 * Body: { approval_key: string, approved: boolean }
 *
 * Called by the frontend when the user confirms or denies a tool approval request.
 * Verifies the key belongs to the authenticated user, then signals the waiting
 * ReAct loop via Redis.
 */
export async function POST(req: NextRequest) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(req));

    let body: unknown;
    try {
      body = await req.json();
    } catch {
      throw new ApiError(400, "VALIDATION_ERROR", "Invalid JSON body");
    }

    if (
      typeof body !== "object" ||
      body === null ||
      typeof (body as Record<string, unknown>).approval_key !== "string" ||
      typeof (body as Record<string, unknown>).approved !== "boolean"
    ) {
      throw new ApiError(
        400,
        "VALIDATION_ERROR",
        "Body must contain { approval_key: string, approved: boolean }",
      );
    }

    const { approval_key, approved } = body as { approval_key: string; approved: boolean };

    // approval_key format: agent:approval:{sessionId}:{toolCallId}
    // Basic format guard to prevent arbitrary Redis key access
    if (!approval_key.startsWith("agent:approval:")) {
      throw new ApiError(403, "FORBIDDEN", "Invalid approval key");
    }

    const redis = await getRedis();
    const raw = await redis.get(approval_key);

    if (!raw) {
      // Key expired or never existed
      throw new ApiError(404, "NOT_FOUND", "Approval request not found or already expired");
    }

    let record: { approved: boolean | null; userId: string };
    try {
      record = JSON.parse(raw) as typeof record;
    } catch {
      throw new ApiError(500, "INTERNAL_ERROR", "Corrupt approval record");
    }

    // Verify the key belongs to the authenticated user
    if (record.userId !== auth.sub) {
      throw new ApiError(403, "FORBIDDEN", "This approval request does not belong to you");
    }

    if (record.approved !== null) {
      throw new ApiError(409, "CONFLICT", "This approval request has already been resolved");
    }

    // Write the decision; keep the remaining TTL by re-setting with the same key
    await redis.set(
      approval_key,
      JSON.stringify({ approved, userId: auth.sub }),
      { KEEPTTL: true },
    );

    return jsonOk({ ok: true, approved });
  } catch (err) {
    if (err instanceof ApiError) return jsonError(err);
    console.error("[approval] unexpected error:", err);
    return jsonError(new ApiError(500, "INTERNAL_ERROR", "Unexpected error"));
  }
}
