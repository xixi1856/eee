import type { NextRequest } from "next/server";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { verifyBindCredentialApiKey } from "@/lib/bind-credential-key";
import { getClientIp } from "@/lib/request-auth";
import { completeBindCredential } from "@/lib/services/credentialService";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const key = req.headers.get("x-platform-bind-key");
    if (!verifyBindCredentialApiKey(key)) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid bind credentials");
    }
    const body = (await req.json()) as {
      bind_challenge_token?: string;
      agent_user_id?: string;
      channel?: string;
    };
    if (
      typeof body.bind_challenge_token !== "string" ||
      typeof body.agent_user_id !== "string" ||
      typeof body.channel !== "string"
    ) {
      throw new ApiError(400, "VALIDATION_ERROR", "Invalid request body");
    }
    const ip = getClientIp(req);
    const result = await completeBindCredential(
      body.bind_challenge_token,
      body.agent_user_id,
      body.channel,
      ip,
    );
    return jsonOk(result);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
