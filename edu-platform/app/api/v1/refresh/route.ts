import { NextResponse, type NextRequest } from "next/server";
import { refreshSession } from "@/lib/services/authService";
import { ApiError } from "@/lib/http/api-error";
import { jsonError } from "@/lib/http/json-response";
import { setAccessTokenCookie } from "@/lib/cookies";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const body = (await req.json()) as { refresh_token?: string };
    if (typeof body.refresh_token !== "string") {
      throw new ApiError(400, "VALIDATION_ERROR", "refresh_token required");
    }
    const out = await refreshSession({ refresh_token: body.refresh_token });
    const res = NextResponse.json({
      token: out.token,
      refresh_token: out.refresh_token,
    });
    setAccessTokenCookie(res, out.token);
    return res;
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
