/**
 * Centralized server config from environment.
 * Security: never log secrets; validate presence at startup of sensitive routes.
 */
function readInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function getJwtSecret(): string {
  const s = process.env.JWT_SECRET;
  if (!s || s.length < 16) {
    throw new Error("JWT_SECRET must be set and at least 16 characters");
  }
  return s;
}

export function getJwtIssuer(): string {
  return process.env.JWT_ISS ?? "edu-platform";
}

export function getAccessTtlSec(): number {
  return readInt("JWT_ACCESS_TTL_SEC", 900);
}

export function getRefreshTtlSec(): number {
  return readInt("JWT_REFRESH_TTL_SEC", 604800);
}

export function getChannelTtlSec(): number {
  return readInt("JWT_CHANNEL_TTL_SEC", 3600);
}

/** Pepper for HMAC of credential codes — not the same as password hashing (argon2). */
export function getCredentialCodePepper(): string {
  const s = process.env.CREDENTIAL_CODE_PEPPER;
  if (!s || s.length < 16) {
    throw new Error(
      "CREDENTIAL_CODE_PEPPER must be set and at least 16 characters",
    );
  }
  return s;
}

export function getBindCredentialApiKey(): string {
  const s = process.env.BIND_CREDENTIAL_API_KEY;
  if (!s || s.length < 16) {
    throw new Error(
      "BIND_CREDENTIAL_API_KEY must be set and at least 16 characters",
    );
  }
  return s;
}

export function getCredentialGenLimitPerHour(): number {
  return readInt("CREDENTIAL_GEN_LIMIT_PER_HOUR", 10);
}

export function getBindFailLimitPerHour(): number {
  return readInt("BIND_FAIL_LIMIT_PER_HOUR", 20);
}

export function getBindBanMinutes(): number {
  return readInt("BIND_BAN_MINUTES", 15);
}

export function getSelfCredentialMaxExpiresMinutes(): number {
  return readInt("SELF_CREDENTIAL_MAX_EXPIRES_MINUTES", 30);
}

export const ACCESS_COOKIE_NAME = "edu_access";
export const REFRESH_COOKIE_NAME = "edu_refresh";

/** Redis URL for bind challenges and bind rate limits. If unset, bind/start returns 503; bind rate limit falls back to Postgres. */
export function getRedisUrl(): string | undefined {
  const u = process.env.REDIS_URL?.trim();
  return u || undefined;
}

/**
 * Seconds before a PARSING/INDEXING material with no DB update is considered abandoned.
 * Mirrors RAG_MATERIAL_STALE_SEC used by the Python worker (default 1800 = 30 min).
 */
export function getMaterialStaleSec(): number {
  return readInt("RAG_MATERIAL_STALE_SEC", 1800);
}

export function getRedisKeyPrefix(): string {
  return process.env.REDIS_KEY_PREFIX?.trim() || "edu:";
}

/** TTL for bind challenge token (seconds). Default 10 minutes. */
export function getBindChallengeTtlSec(): number {
  return readInt("BIND_CHALLENGE_TTL_SEC", 600);
}

/**
 * Signing secret for short-lived bind challenge JWTs.
 * Falls back to JWT_SECRET if BIND_CHALLENGE_SECRET is not set.
 */
export function getBindChallengeSecret(): string {
  const s = process.env.BIND_CHALLENGE_SECRET?.trim();
  if (s && s.length >= 16) return s;
  return getJwtSecret();
}

/**
 * How many trusted reverse proxies sit in front of Next (for client IP).
 * 0 = do not trust X-Forwarded-For for client identity; prefer X-Real-IP from your gateway.
 * Greater than zero: take the client IP from X-Forwarded-For counting N hops from the right.
 */
export function getTrustProxyHops(): number {
  const v = process.env.TRUST_PROXY_HOPS;
  if (v === undefined || v === "") return 0;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/** Shared secret for server-to-server calls (e.g. Agent course RAG access). Min 16 chars. */
export function getInternalApiKeyOrNull(): string | null {
  const s = process.env.INTERNAL_API_KEY?.trim();
  if (!s || s.length < 16) return null;
  return s;
}

/** Base URL for Python EduAgent HTTP API (no trailing slash). */
export function getEduAgentBaseUrl(): string | null {
  const u = process.env.EDU_AGENT_BASE_URL?.trim().replace(/\/+$/, "");
  return u || null;
}

/** Optional Bearer token for EduAgent HTTP (when Agent enforces API key). */
export function getEduAgentApiKey(): string | null {
  const s = process.env.EDU_AGENT_API_KEY?.trim();
  if (!s || s.length < 8) return null;
  return s;
}

export function getRagTaskStreamName(): string {
  const q = process.env.RAG_TASK_STREAM_NAME?.trim();
  return q || "edu:rag:tasks:stream";
}

export function getRagTaskStreamGroup(): string {
  return process.env.RAG_TASK_STREAM_GROUP?.trim() || "edu-rag-workers";
}

export function getRagTaskConsumerName(): string {
  const c = process.env.RAG_TASK_CONSUMER_NAME?.trim();
  if (c) return c;
  return `edu-next-${process.pid}`;
}

export function getMaterialMaxUploadBytes(): number {
  return readInt("MATERIAL_MAX_UPLOAD_BYTES", 52_428_800);
}

export type MinioConfig = {
  endpoint: string;
  region: string;
  accessKeyId: string;
  secretAccessKey: string;
  bucket: string;
  useSsl: boolean;
};

/** MinIO / S3-compatible storage — required for material uploads. */
export function getMinioConfig(): MinioConfig {
  const endpoint = process.env.MINIO_ENDPOINT?.trim();
  const accessKeyId = process.env.MINIO_ACCESS_KEY?.trim();
  const secretAccessKey = process.env.MINIO_SECRET_KEY?.trim();
  const bucket = process.env.MINIO_BUCKET?.trim();
  if (!endpoint || !accessKeyId || !secretAccessKey || !bucket) {
    throw new Error(
      "MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET must be set for material storage",
    );
  }
  const region = process.env.MINIO_REGION?.trim() || "us-east-1";
  const useSsl =
    (process.env.MINIO_USE_SSL ?? "true").toLowerCase() === "true";
  return {
    endpoint,
    region,
    accessKeyId,
    secretAccessKey,
    bucket,
    useSsl,
  };
}
