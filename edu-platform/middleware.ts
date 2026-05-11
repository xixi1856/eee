import { NextResponse, type NextRequest } from "next/server";
import { verifyAccessToken } from "@/lib/jwt";
import { ACCESS_COOKIE_NAME } from "@/lib/config";

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const token = request.cookies.get(ACCESS_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  try {
    await verifyAccessToken(token);
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
