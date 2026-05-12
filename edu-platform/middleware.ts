import { NextResponse, type NextRequest } from "next/server";
import { verifyAccessToken } from "@/lib/jwt";
import {
  ACCESS_COOKIE_NAME,
  REFRESH_COOKIE_NAME,
  getAccessTtlSec,
  getRefreshTtlSec,
} from "@/lib/config";

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const accessToken = request.cookies.get(ACCESS_COOKIE_NAME)?.value;

  // Fast path: valid access token
  if (accessToken) {
    try {
      await verifyAccessToken(accessToken);
      return NextResponse.next();
    } catch {
      // Expired or invalid — fall through to silent refresh
    }
  }

  // Silent refresh: try the HttpOnly refresh token cookie
  const refreshToken = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
  if (refreshToken) {
    try {
      const refreshUrl = new URL("/api/v1/refresh", request.url);
      const refreshRes = await fetch(refreshUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Pass refresh token in body so the route handler can validate it
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (refreshRes.ok) {
        const data = (await refreshRes.json()) as {
          token?: string;
          refresh_token?: string;
        };
        if (data.token && data.refresh_token) {
          const secure = request.nextUrl.protocol === "https:";
          const res = NextResponse.next();
          res.cookies.set(ACCESS_COOKIE_NAME, data.token, {
            httpOnly: true,
            sameSite: "lax",
            path: "/",
            maxAge: getAccessTtlSec(),
            secure,
          });
          res.cookies.set(REFRESH_COOKIE_NAME, data.refresh_token, {
            httpOnly: true,
            sameSite: "lax",
            path: "/",
            maxAge: getRefreshTtlSec(),
            secure,
          });
          return res;
        }
      }
    } catch {
      // Network error or parse failure — redirect to login
    }
  }

  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  matcher: [
    "/user",
    "/user/:path*",
    "/credentials",
    "/credentials/:path*",
    "/courses",
    "/courses/:path*",
  ],
};
