import { verifyAccessToken, type AccessJwtPayload } from "@/lib/jwt";

export type AuthContext = AccessJwtPayload;

export function parseBearerToken(
  authorization: string | null,
): string | null {
  if (!authorization || !authorization.startsWith("Bearer ")) {
    return null;
  }
  const token = authorization.slice("Bearer ".length).trim();
  return token.length > 0 ? token : null;
}

export async function getAuthFromBearer(
  authorization: string | null,
): Promise<AuthContext | null> {
  const token = parseBearerToken(authorization);
  if (!token) return null;
  try {
    return await verifyAccessToken(token);
  } catch {
    return null;
  }
}
