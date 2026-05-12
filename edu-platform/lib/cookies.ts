import type { NextResponse } from "next/server";
import { ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, getAccessTtlSec, getRefreshTtlSec } from "@/lib/config";

/** HttpOnly cookie so middleware can guard pages without reading localStorage. */
export function setAccessTokenCookie(res: NextResponse, token: string): void {
  const maxAge = getAccessTtlSec();
  const secure = process.env.NODE_ENV === "production";
  res.cookies.set(ACCESS_COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge,
    secure,
  });
}

/** HttpOnly refresh token cookie — only sent to /api/v1/refresh. */
export function setRefreshTokenCookie(res: NextResponse, token: string): void {
  const maxAge = getRefreshTtlSec();
  const secure = process.env.NODE_ENV === "production";
  res.cookies.set(REFRESH_COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge,
    secure,
  });
}

/** Clear both access and refresh cookies (logout / auth failure). */
export function clearAuthCookies(res: NextResponse): void {
  res.cookies.set(ACCESS_COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
  res.cookies.set(REFRESH_COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
}
