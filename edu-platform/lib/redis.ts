import { createClient, type RedisClientType } from "redis";
import { getRedisUrl } from "@/lib/config";

let client: RedisClientType | null = null;
let connectPromise: Promise<RedisClientType> | null = null;

export function isRedisConfigured(): boolean {
  return Boolean(getRedisUrl());
}

export async function getRedis(): Promise<RedisClientType> {
  const url = getRedisUrl();
  if (!url) {
    throw new Error("REDIS_URL is not configured");
  }
  if (client?.isOpen) {
    return client;
  }
  if (connectPromise) {
    return connectPromise;
  }
  connectPromise = (async () => {
    const c = createClient({ url });
    c.on("error", () => {
      /* avoid unhandled rejection; callers handle command errors */
    });
    await c.connect();
    client = c as RedisClientType;
    return client;
  })();
  try {
    return await connectPromise;
  } finally {
    connectPromise = null;
  }
}
