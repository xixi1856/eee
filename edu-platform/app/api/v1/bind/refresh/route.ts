import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { verifyBindCredentialApiKey } from "@/lib/bind-credential-key";
import { refreshChannelToken } from "@/lib/services/credentialService";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const key = req.headers.get("x-platform-bind-key");
    if (!verifyBindCredentialApiKey(key)) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid bind credentials");
    }
    const body = (await req.json()) as { agent_user_id?: string };
    if (typeof body.agent_user_id !== "string" || !body.agent_user_id.trim()) {
      throw new ApiError(400, "VALIDATION_ERROR", "agent_user_id is required");
    }
    const result = await refreshChannelToken(body.agent_user_id);
    return jsonOk(result);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
