import crypto from "node:crypto";
import { getBindChallengeTtlSec, getRedisKeyPrefix } from "@/lib/config";
import { ApiError } from "@/lib/http/api-error";
import { getRedis, isRedisConfigured } from "@/lib/redis";

function challengeKey(token: string): string {
  return `${getRedisKeyPrefix()}bind:ch:${token}`;
}

export async function createBindChallenge(codeHash: string): Promise<string> {
  if (!isRedisConfigured()) {
    throw new ApiError(
      503,
      "SERVICE_UNAVAILABLE",
      "Redis is required for the bind challenge flow; set REDIS_URL",
    );
  }
  const r = await getRedis();
  const token = crypto.randomBytes(32).toString("hex");
  await r.set(challengeKey(token), codeHash, { EX: getBindChallengeTtlSec() });
  return token;
}

/** Atomically read and delete challenge; returns codeHash or null if missing. */
export async function consumeBindChallenge(
  challengeToken: string,
): Promise<string | null> {
  if (!isRedisConfigured()) {
    return null;
  }
  const t = challengeToken.trim();
  if (!/^[a-f0-9]{64}$/i.test(t)) {
    return null;
  }
  const r = await getRedis();
  const v = await r.getDel(challengeKey(t));
  return typeof v === "string" && v.length > 0 ? v : null;
}
