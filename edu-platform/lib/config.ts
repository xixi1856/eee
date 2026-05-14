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

export const ACCESS_COOKIE_NAME = "edu_access";
export const REFRESH_COOKIE_NAME = "edu_refresh";
/**
 * Seconds before a PARSING/INDEXING material with no DB update is considered abandoned.
 * Mirrors RAG_MATERIAL_STALE_SEC used by the Python worker (default 1800 = 30 min).
 */
export function getMaterialStaleSec(): number {
  return readInt("RAG_MATERIAL_STALE_SEC", 1800);
}

/**
 * How many trusted reverse proxies sit in front of Next (for client IP).
 * 0 = do not trust X-Forwarded-For for client identity; prefer X-Real-IP from your gateway.
 */
export function getTrustProxyHops(): number {
  const v = process.env.TRUST_PROXY_HOPS;
  if (v === undefined || v === "") return 0;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/** Redis URL for RAG task stream and material processing. */
export function getRedisUrl(): string | undefined {
  const u = process.env.REDIS_URL?.trim();
  return u || undefined;
}

export function getRedisKeyPrefix(): string {
  return process.env.REDIS_KEY_PREFIX?.trim() || "edu:";
}

/** Shared secret for server-to-server calls (e.g. Agent course RAG access). Min 16 chars. */
export function getInternalApiKeyOrNull(): string | null {
  const s = process.env.INTERNAL_API_KEY?.trim();
  if (!s || s.length < 16) return null;
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

export function getCronStreamName(): string {
  return process.env.CRON_STREAM_NAME?.trim() || "edu:cron:stream";
}

export function getCronStreamGroup(): string {
  return process.env.CRON_STREAM_GROUP?.trim() || "edu-cron-workers";
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
