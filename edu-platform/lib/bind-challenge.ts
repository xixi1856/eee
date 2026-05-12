import { ApiError } from "@/lib/http/api-error";
import { signBindChallengeToken, verifyBindChallengeToken } from "@/lib/jwt";

/**
 * Create a short-lived bind challenge token that embeds the codeHash.
 * Uses a signed JWT so no external storage (Redis) is required.
 */
export async function createBindChallenge(codeHash: string): Promise<string> {
  return signBindChallengeToken(codeHash);
}

/**
 * Verify a bind challenge token and return the embedded codeHash.
 * Returns null if the token is missing, malformed, or expired.
 * NOTE: Unlike the previous Redis getDel approach this token is not
 * single-use at the storage level. Replay protection is provided by
 * the DB transaction in completeBindCredential (credential status ACTIVE→USED).
 */
export async function consumeBindChallenge(
  challengeToken: string,
): Promise<string | null> {
  if (!challengeToken || typeof challengeToken !== "string") return null;
  try {
    return await verifyBindChallengeToken(challengeToken.trim());
  } catch {
    return null;
  }
}
