import { NextResponse, type NextRequest } from "next/server";
import { refreshSession } from "@/lib/services/authService";
import { ApiError } from "@/lib/http/api-error";
import { jsonError } from "@/lib/http/json-response";
import { setAccessTokenCookie, setRefreshTokenCookie } from "@/lib/cookies";
import { REFRESH_COOKIE_NAME } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    // Prefer HttpOnly cookie; fall back to JSON body for backward compatibility
    const cookieToken = req.cookies.get(REFRESH_COOKIE_NAME)?.value;
    let refreshTokenPlain: string;
    if (cookieToken) {
      refreshTokenPlain = cookieToken;
    } else {
      const body = (await req.json().catch(() => ({}))) as { refresh_token?: string };
      if (typeof body.refresh_token !== "string" || !body.refresh_token) {
        throw new ApiError(400, "VALIDATION_ERROR", "refresh_token required");
      }
      refreshTokenPlain = body.refresh_token;
    }
    const out = await refreshSession({ refresh_token: refreshTokenPlain });
    const res = NextResponse.json({
      token: out.token,
      refresh_token: out.refresh_token,
    });
    setAccessTokenCookie(res, out.token);
    setRefreshTokenCookie(res, out.refresh_token);
    return res;
  } catch (e) {
    if (e instanceof ApiError) return jsonError(e);
    return jsonError(
      new ApiError(500, "INTERNAL_ERROR", "Internal server error"),
    );
  }
}
