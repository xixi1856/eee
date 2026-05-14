/** After Phase 1: platform user_id is passed directly — no identity mapping needed. */
export async function resolvePlatformUserFromAgentQuery(
  userIdParam: string,
): Promise<string> {
  return userIdParam;
}
