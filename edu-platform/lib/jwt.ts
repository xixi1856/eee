import * as jose from "jose";
import type { UserRole } from "@prisma/client";
import {
  getAccessTtlSec,
  getBindChallengeTtlSec,
  getBindChallengeSecret,
  getChannelTtlSec,
  getJwtIssuer,
  getJwtSecret,
} from "@/lib/config";

export type AccessJwtPayload = {
  sub: string;
  username: string;
  role: UserRole;
  agent_user_id?: string;
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
    agent_user_id: payload.agent_user_id,
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
    agent_user_id:
      typeof payload.agent_user_id === "string"
        ? payload.agent_user_id
        : undefined,
  };
}

export type ChannelJwtPayload = {
  platform_user_id: string;
  agent_user_id: string;
  channel: string;
};

export async function signChannelToken(
  payload: ChannelJwtPayload,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const exp = now + getChannelTtlSec();
  return new jose.SignJWT({
    typ: "channel",
    platform_user_id: payload.platform_user_id,
    agent_user_id: payload.agent_user_id,
    channel: payload.channel,
  })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(payload.platform_user_id)
    .setIssuedAt(now)
    .setExpirationTime(exp)
    .setIssuer(getJwtIssuer())
    .sign(secretKey());
}

export async function verifyChannelToken(
  token: string,
): Promise<ChannelJwtPayload> {
  const { payload } = await jose.jwtVerify(token, secretKey(), {
    issuer: getJwtIssuer(),
    algorithms: ["HS256"],
  });
  const typ = payload.typ;
  if (typ !== "channel") {
    throw new Error("Not a channel token");
  }
  const platform_user_id = payload.platform_user_id;
  const agent_user_id = payload.agent_user_id;
  const channel = payload.channel;
  if (
    typeof platform_user_id !== "string" ||
    typeof agent_user_id !== "string" ||
    typeof channel !== "string"
  ) {
    throw new Error("Invalid channel payload");
  }
  return { platform_user_id, agent_user_id, channel };
}

// ── Bind challenge JWT ──────────────────────────────────────────────────────
// Short-lived token that carries the credential codeHash between bind/start
// and bind/complete without requiring Redis storage.

const BIND_CHALLENGE_ISS = "edu-platform-bind";

function bindChallengeKey(): Uint8Array {
  return new TextEncoder().encode(getBindChallengeSecret());
}

/** Sign a short-lived bind challenge JWT containing the credential codeHash. */
export async function signBindChallengeToken(codeHash: string): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const exp = now + getBindChallengeTtlSec();
  return new jose.SignJWT({ codeHash })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt(now)
    .setExpirationTime(exp)
    .setIssuer(BIND_CHALLENGE_ISS)
    .sign(bindChallengeKey());
}

/** Verify a bind challenge JWT and return the embedded codeHash. Throws on failure. */
export async function verifyBindChallengeToken(token: string): Promise<string> {
  const { payload } = await jose.jwtVerify(token, bindChallengeKey(), {
    issuer: BIND_CHALLENGE_ISS,
    algorithms: ["HS256"],
  });
  const codeHash = payload.codeHash;
  if (typeof codeHash !== "string" || !codeHash) {
    throw new Error("Invalid bind challenge payload");
  }
  return codeHash;
}
