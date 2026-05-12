import { NextResponse, type NextRequest } from "next/server";
import { loginUser } from "@/lib/services/authService";
import { ApiError } from "@/lib/http/api-error";
import { jsonError } from "@/lib/http/json-response";
import { setAccessTokenCookie, setRefreshTokenCookie } from "@/lib/cookies";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const body = (await req.json()) as {
      username?: string;
      password?: string;
    };
    if (typeof body.username !== "string" || typeof body.password !== "string") {
      throw new ApiError(400, "VALIDATION_ERROR", "Invalid request body");
    }
    const out = await loginUser({
      username: body.username,
      password: body.password,
    });
    const res = NextResponse.json({
      token: out.token,
      refresh_token: out.refresh_token,
      user: out.user,
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
