import type { NextRequest } from "next/server";
import { changePassword } from "@/lib/services/authService";
import { jsonOk, jsonError } from "@/lib/http/json-response";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import type { ChangePasswordBody } from "@/lib/dto/auth.dto";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const ctx = requireAuthenticated(await getAuthFromRequest(req));
    const body = (await req.json()) as {
      current_password?: string;
      new_password?: string;
    };
    if (
      typeof body.current_password !== "string" ||
      typeof body.new_password !== "string"
    ) {
      throw new ApiError(400, "VALIDATION_ERROR", "Invalid request body");
    }
    const payload: ChangePasswordBody = {
      current_password: body.current_password,
      new_password: body.new_password,
    };
    await changePassword(ctx.sub, payload);
    return jsonOk({ ok: true });
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
