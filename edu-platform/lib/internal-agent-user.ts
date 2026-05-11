import { prisma } from "@/lib/db";

/** Resolve Agent ``user_id`` query param to platform ``users.id`` when mapping exists. */
export async function resolvePlatformUserFromAgentQuery(
  userIdParam: string,
): Promise<string> {
  const row = await prisma.agentIdentityMapping.findUnique({
    where: { agentUserId: userIdParam },
    select: { platformUserId: true },
  });
  if (row) return row.platformUserId;
  return userIdParam;
}
