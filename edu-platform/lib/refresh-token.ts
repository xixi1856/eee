import { createHmac, randomBytes } from "crypto";
import { getJwtSecret } from "@/lib/config";

/** Opaque refresh token: only HMAC digest is persisted (never the raw value). */
export function generateRefreshTokenPlain(): string {
  return randomBytes(48).toString("base64url");
}

export function hashRefreshToken(plain: string): string {
  return createHmac("sha256", getJwtSecret())
    .update(plain, "utf8")
    .digest("hex");
}
