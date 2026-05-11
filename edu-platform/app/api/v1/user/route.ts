import type { NextRequest } from "next/server";
import { getUserProfile, updateUserProfile } from "@/lib/services/userService";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import type { UpdateUserBody } from "@/lib/dto/user.dto";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    const user = await getUserProfile(ctx.sub);
    return jsonOk(user);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}

export async function PUT(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    const body = (await req.json()) as UpdateUserBody;
    const user = await updateUserProfile(ctx.sub, body);
    return jsonOk(user);
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
