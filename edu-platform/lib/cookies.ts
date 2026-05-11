import type { NextResponse } from "next/server";
import { ACCESS_COOKIE_NAME, getAccessTtlSec } from "@/lib/config";

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
