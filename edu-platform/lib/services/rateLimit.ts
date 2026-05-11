import { BindAttemptKeyType } from "@prisma/client";
import { prisma } from "@/lib/db";
import {
  getBindBanMinutes,
  getBindFailLimitPerHour,
  getCredentialGenLimitPerHour,
  getRedisKeyPrefix,
} from "@/lib/config";
import { ApiError } from "@/lib/http/api-error";
import { getRedis, isRedisConfigured } from "@/lib/redis";

const HOUR_MS = 60 * 60 * 1000;

export async function countCredentialsCreatedSince(
  userId: string,
  since: Date,
): Promise<number> {
  return prisma.credential.count({
    where: { userId, createdAt: { gte: since } },
  });
}

export async function assertCredentialGenerationAllowed(
  userId: string,
): Promise<void> {
  const since = new Date(Date.now() - HOUR_MS);
  const limit = getCredentialGenLimitPerHour();
  const n = await countCredentialsCreatedSince(userId, since);
  if (n >= limit) {
    throw new ApiError(
      429,
      "RATE_LIMITED",
      "Too many credentials created in the last hour",
      { limit },
    );
  }
}

function bindBanKey(ip: string): string {
  return `${getRedisKeyPrefix()}bind:ban:${ip.slice(0, 128)}`;
}

function bindFailZKey(ip: string): string {
  return `${getRedisKeyPrefix()}bind:failz:${ip.slice(0, 128)}`;
}

async function assertBindAttemptAllowedRedis(clientIp: string): Promise<void> {
  const ip = clientIp.slice(0, 128);
  const r = await getRedis();
  const banned = await r.exists(bindBanKey(ip));
  if (banned) {
    throw new ApiError(
      429,
      "RATE_LIMITED",
      `Too many failed bind attempts; retry after ${getBindBanMinutes()} minutes`,
      { retry_after_minutes: getBindBanMinutes() },
    );
  }
  const now = Date.now();
  const windowStart = now - HOUR_MS;
  const zkey = bindFailZKey(ip);
  await r.zRemRangeByScore(zkey, 0, windowStart);
  const n = await r.zCard(zkey);
  const limit = getBindFailLimitPerHour();
  if (n >= limit) {
    throw new ApiError(
      429,
      "RATE_LIMITED",
      `Too many failed bind attempts; retry after ${getBindBanMinutes()} minutes`,
      { retry_after_minutes: getBindBanMinutes() },
    );
  }
}

async function recordBindFailureRedis(ip: string): Promise<void> {
  const slice = ip.slice(0, 128);
  const r = await getRedis();
  const zkey = bindFailZKey(slice);
  const now = Date.now();
  const member = `${now}:${Math.random().toString(36).slice(2, 10)}`;
  await r.zAdd(zkey, { score: now, value: member });
  await r.zRemRangeByScore(zkey, 0, now - HOUR_MS);
  const n = await r.zCard(zkey);
  const limit = getBindFailLimitPerHour();
  if (n >= limit) {
    const banSec = getBindBanMinutes() * 60;
    await r.set(bindBanKey(slice), "1", { EX: Math.max(banSec, 60) });
  }
}

async function assertBindAttemptAllowedPrisma(clientIp: string): Promise<void> {
  const since = new Date(Date.now() - HOUR_MS);
  const limit = getBindFailLimitPerHour();
  const n = await prisma.credentialBindAttempt.count({
    where: {
      keyType: BindAttemptKeyType.IP,
      keyValue: clientIp.slice(0, 128),
      createdAt: { gte: since },
    },
  });
  if (n >= limit) {
    throw new ApiError(
      429,
      "RATE_LIMITED",
      `Too many failed bind attempts; retry after ${getBindBanMinutes()} minutes`,
      { retry_after_minutes: getBindBanMinutes() },
    );
  }
}

export async function recordBindFailure(ip: string): Promise<void> {
  if (isRedisConfigured()) {
    try {
      await recordBindFailureRedis(ip);
      return;
    } catch {
      /* fall through to prisma */
    }
  }
  await prisma.credentialBindAttempt.create({
    data: {
      keyType: BindAttemptKeyType.IP,
      keyValue: ip.slice(0, 128),
    },
  });
}

export async function assertBindAttemptAllowed(
  clientIp: string,
): Promise<void> {
  if (isRedisConfigured()) {
    try {
      await assertBindAttemptAllowedRedis(clientIp);
      return;
    } catch (e) {
      if (e instanceof ApiError) {
        throw e;
      }
    }
  }
  await assertBindAttemptAllowedPrisma(clientIp);
}
