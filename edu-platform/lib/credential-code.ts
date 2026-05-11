import { createHmac, randomBytes } from "crypto";
import { getCredentialCodePepper } from "@/lib/config";

const CHARSET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

/**
 * Credential codes use HMAC-SHA256 with a server pepper (fast, fixed-length digest).
 * Passwords use argon2id elsewhere — different threat model (high-entropy short code vs user password).
 */
export function hashCredentialCode(plain: string): string {
  const pepper = getCredentialCodePepper();
  return createHmac("sha256", pepper).update(plain, "utf8").digest("hex");
}

export function generatePlainCredentialCode(): string {
  const bytes = randomBytes(8);
  let out = "";
  for (let i = 0; i < 8; i++) {
    out += CHARSET[bytes[i]! % CHARSET.length];
  }
  return out;
}
