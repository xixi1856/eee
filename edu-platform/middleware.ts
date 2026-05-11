import { NextResponse, type NextRequest } from "next/server";
import { verifyAccessToken } from "@/lib/jwt";
import { ACCESS_COOKIE_NAME } from "@/lib/config";
import { UserRole } from "@prisma/client";

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(ACCESS_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  try {
    const payload = await verifyAccessToken(token);
    const path = request.nextUrl.pathname;
    if (
      path.startsWith("/credentials") &&
      payload.role === UserRole.TEACHER
    ) {
      return NextResponse.redirect(new URL("/user", request.url));
    }
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/login", request.url));
  }
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
