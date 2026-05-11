import { timingSafeEqual } from "crypto";
import { getBindCredentialApiKey } from "@/lib/config";

/**
 * Constant-time comparison for the Agent bind API key.
 * Never log the provided key or the expected secret.
 */
export function verifyBindCredentialApiKey(headerValue: string | null): boolean {
  try {
    const expected = getBindCredentialApiKey();
    if (!headerValue) return false;
    const a = Buffer.from(headerValue, "utf8");
    const b = Buffer.from(expected, "utf8");
    if (a.length !== b.length) return false;
    return timingSafeEqual(a, b);
  } catch {
    return false;
  }
}
