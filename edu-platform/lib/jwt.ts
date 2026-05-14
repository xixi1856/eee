import * as jose from "jose";
import type { UserRole } from "@prisma/client";
import {
  getAccessTtlSec,
  getJwtIssuer,
  getJwtSecret,
} from "@/lib/config";

export type AccessJwtPayload = {
  sub: string;
  username: string;
  role: UserRole;
};

function secretKey(): Uint8Array {
  return new TextEncoder().encode(getJwtSecret());
}

export async function signAccessToken(
  payload: AccessJwtPayload,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const exp = now + getAccessTtlSec();
  const jwt = await new jose.SignJWT({
    username: payload.username,
    role: payload.role,
  })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(payload.sub)
    .setIssuedAt(now)
    .setExpirationTime(exp)
    .setIssuer(getJwtIssuer())
    .sign(secretKey());
  return jwt;
}

export async function verifyAccessToken(
  token: string,
): Promise<AccessJwtPayload> {
  const { payload } = await jose.jwtVerify(token, secretKey(), {
    issuer: getJwtIssuer(),
    algorithms: ["HS256"],
  });
  const sub = payload.sub;
  const username = payload.username;
  const role = payload.role;
  if (typeof sub !== "string" || typeof username !== "string" || !role) {
    throw new Error("Invalid access token payload");
  }
  return {
    sub,
    username,
    role: role as UserRole,
  };
}


