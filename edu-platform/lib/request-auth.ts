import type { NextRequest } from "next/server";
import { verifyAccessToken, type AccessJwtPayload } from "@/lib/jwt";
import { ACCESS_COOKIE_NAME, getTrustProxyHops } from "@/lib/config";
import { getAuthFromBearer } from "@/lib/middleware-helpers";

export async function getAuthFromRequest(
  req: NextRequest,
): Promise<AccessJwtPayload | null> {
  const fromBearer = await getAuthFromBearer(req.headers.get("authorization"));
  if (fromBearer) return fromBearer;
  const cookieToken = req.cookies.get(ACCESS_COOKIE_NAME)?.value;
  if (!cookieToken) return null;
  try {
    return await verifyAccessToken(cookieToken);
  } catch {
    return null;
  }
}

/**
 * Client IP for rate limiting. With TRUST_PROXY_HOPS=0 (default), X-Forwarded-For is ignored
 * (prevents trivial spoofing); use X-Real-IP from a trusted gateway or "unknown".
 */
export function getClientIp(req: NextRequest): string {
  const hops = getTrustProxyHops();
  const xff = req.headers.get("x-forwarded-for");
  if (xff && hops > 0) {
    const parts = xff
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const idx = parts.length - 1 - hops;
    if (idx >= 0 && parts[idx]) {
      return parts[idx]!.slice(0, 128);
    }
  }
  const real = req.headers.get("x-real-ip")?.trim();
  if (real) {
    return real.slice(0, 128);
  }
  return "unknown";
}
