/**
 * Server-side auth utilities (passwords, JWTs, refresh opaque tokens).
 * Edge middleware must only import from `@/lib/jwt` to avoid bundling argon2.
 */
export { hashPassword, verifyPassword } from "@/lib/password";
export {
  signAccessToken,
  verifyAccessToken,
  type AccessJwtPayload,
} from "@/lib/jwt";
export { generateRefreshTokenPlain, hashRefreshToken } from "@/lib/refresh-token";
