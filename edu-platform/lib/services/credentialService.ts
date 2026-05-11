import {
  CredentialStatus,
  Prisma,
  UserRole,
  type Credential,
} from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import {
  generatePlainCredentialCode,
  hashCredentialCode,
} from "@/lib/credential-code";
import { signChannelToken } from "@/lib/jwt";
import { createBindChallenge, consumeBindChallenge } from "@/lib/bind-challenge";
import {
  assertBindAttemptAllowed,
  assertCredentialGenerationAllowed,
  recordBindFailure,
} from "@/lib/services/rateLimit";
import { getSelfCredentialMaxExpiresMinutes } from "@/lib/config";
import type {
  BindCredentialResponseDto,
  CredentialCreatedDto,
  CredentialListItemDto,
} from "@/lib/dto/credential.dto";

type DbClient = typeof prisma | Prisma.TransactionClient;

function toListItem(c: Credential): CredentialListItemDto {
  return {
    id: c.id,
    user_id: c.userId,
    status: c.status,
    created_at: c.createdAt.toISOString(),
    expires_at: c.expiresAt?.toISOString() ?? null,
    used_at: c.usedAt?.toISOString() ?? null,
    bound_at: c.boundAt?.toISOString() ?? null,
    bound_agent_user_id: c.boundAgentUserId,
  };
}

async function markExpiredForUser(userId: string): Promise<void> {
  await prisma.credential.updateMany({
    where: {
      userId,
      status: CredentialStatus.ACTIVE,
      expiresAt: { lt: new Date() },
    },
    data: { status: CredentialStatus.EXPIRED },
  });
}

async function markExpiredAdmin(filters: {
  userId?: string;
}): Promise<void> {
  const where: Prisma.CredentialWhereInput = {
    status: CredentialStatus.ACTIVE,
    expiresAt: { lt: new Date() },
  };
  if (filters.userId) where.userId = filters.userId;
  await prisma.credential.updateMany({
    where,
    data: { status: CredentialStatus.EXPIRED },
  });
}

async function allocateUniqueCredentialCore(
  db: DbClient,
  userId: string,
  expiresAt: Date,
  skipGenerationLimit: boolean,
): Promise<CredentialCreatedDto> {
  if (!skipGenerationLimit) {
    await assertCredentialGenerationAllowed(userId);
  }
  let plain = "";
  let codeHash = "";
  for (let attempt = 0; attempt < 12; attempt++) {
    plain = generatePlainCredentialCode();
    codeHash = hashCredentialCode(plain);
    const exists = await db.credential.findUnique({
      where: { codeHash },
    });
    if (!exists) break;
    if (attempt === 11) {
      throw new ApiError(
        500,
        "INTERNAL_ERROR",
        "Could not allocate credential code",
      );
    }
  }
  await db.credential.create({
    data: {
      userId,
      codeHash,
      status: CredentialStatus.ACTIVE,
      expiresAt,
    },
  });
  return {
    code: plain,
    expires_at: expiresAt.toISOString(),
    status: CredentialStatus.ACTIVE,
  };
}

/** Registration-time code (student or teacher); skips per-hour gen limit like legacy student path. */
export async function allocateRegistrationCredentialInTransaction(
  tx: Prisma.TransactionClient,
  userId: string,
  expiresAt: Date,
): Promise<CredentialCreatedDto> {
  return allocateUniqueCredentialCore(tx, userId, expiresAt, true);
}

async function allocateUniqueCredential(
  userId: string,
  expiresAt: Date,
): Promise<CredentialCreatedDto> {
  return allocateUniqueCredentialCore(prisma, userId, expiresAt, false);
}

export async function createAdminCredentialForUser(
  adminRole: UserRole,
  targetUserId: string,
  expiresInMinutesInput: number | undefined,
): Promise<CredentialCreatedDto> {
  if (adminRole !== UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "ADMIN role required");
  }
  const target = await prisma.user.findFirst({
    where: { id: targetUserId, isActive: true },
  });
  if (!target) {
    throw new ApiError(404, "NOT_FOUND", "Target user not found");
  }
  const expiresIn = expiresInMinutesInput ?? 30;
  if (expiresIn < 1 || expiresIn > 60 * 24 * 365) {
    throw new ApiError(
      400,
      "VALIDATION_ERROR",
      "expires_in_minutes must be between 1 and 525600",
    );
  }
  const expiresAt = new Date(Date.now() + expiresIn * 60 * 1000);
  const dto = await allocateUniqueCredential(targetUserId, expiresAt);
  return { ...dto, user_id: targetUserId };
}

export async function listMyCredentials(
  userId: string,
): Promise<CredentialListItemDto[]> {
  await markExpiredForUser(userId);
  const rows = await prisma.credential.findMany({
    where: { userId },
    orderBy: { createdAt: "desc" },
  });
  return rows.map(toListItem);
}

export async function listAdminCredentials(
  adminRole: UserRole,
  filters: { user_id?: string; status?: CredentialStatus },
): Promise<CredentialListItemDto[]> {
  if (adminRole !== UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "ADMIN role required");
  }
  await markExpiredAdmin({ userId: filters.user_id });
  const where: Prisma.CredentialWhereInput = {};
  if (filters.user_id) where.userId = filters.user_id;
  if (filters.status) where.status = filters.status;
  const rows = await prisma.credential.findMany({
    where,
    orderBy: { createdAt: "desc" },
    take: 500,
  });
  return rows.map(toListItem);
}

export async function adminRevokeCredential(
  adminRole: UserRole,
  credentialId: string,
): Promise<void> {
  if (adminRole !== UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "ADMIN role required");
  }
  const c = await prisma.credential.findUnique({
    where: { id: credentialId },
  });
  if (!c) {
    throw new ApiError(404, "NOT_FOUND", "Credential not found");
  }
  if (c.status === CredentialStatus.USED) {
    throw new ApiError(409, "CONFLICT", "Cannot revoke a used credential");
  }
  if (c.status === CredentialStatus.REVOKED) {
    return;
  }
  if (c.status !== CredentialStatus.ACTIVE) {
    throw new ApiError(409, "CONFLICT", "Credential cannot be revoked");
  }
  await prisma.credential.update({
    where: { id: credentialId },
    data: { status: CredentialStatus.REVOKED },
  });
}

type BindTxOk = { ok: true; platformUserId: string };
type BindTxFail = { ok: false; reason?: "agent_user_bound_elsewhere" };

async function performBindTransaction(
  codeHash: string,
  agentUserId: string,
  channel: string,
): Promise<BindTxOk | BindTxFail> {
  return prisma.$transaction(async (tx) => {
    const cred = await tx.credential.findFirst({
      where: { codeHash },
    });
    if (!cred) {
      return { ok: false as const };
    }
    if (cred.status !== CredentialStatus.ACTIVE) {
      return { ok: false as const };
    }
    if (cred.expiresAt && cred.expiresAt < new Date()) {
      await tx.credential.update({
        where: { id: cred.id },
        data: { status: CredentialStatus.EXPIRED },
      });
      return { ok: false as const };
    }
    const existingAgent = await tx.agentIdentityMapping.findUnique({
      where: { agentUserId },
    });
    if (existingAgent && existingAgent.platformUserId !== cred.userId) {
      return { ok: false as const, reason: "agent_user_bound_elsewhere" };
    }
    const updated = await tx.credential.updateMany({
      where: {
        id: cred.id,
        status: CredentialStatus.ACTIVE,
      },
      data: {
        status: CredentialStatus.USED,
        usedAt: new Date(),
        boundAgentUserId: agentUserId,
        boundAt: new Date(),
      },
    });
    if (updated.count !== 1) {
      return { ok: false as const };
    }
    await tx.agentIdentityMapping.upsert({
      where: { platformUserId: cred.userId },
      create: {
        platformUserId: cred.userId,
        agentUserId,
        channel,
        boundAt: new Date(),
      },
      update: {
        agentUserId,
        channel,
        boundAt: new Date(),
      },
    });
    return { ok: true as const, platformUserId: cred.userId };
  });
}

/** Step 1–2: validate code, return opaque challenge token (Redis). Does not consume the credential. */
export async function startBindCredential(
  codeRaw: string,
  clientIp: string,
): Promise<{ bind_challenge_token: string }> {
  await assertBindAttemptAllowed(clientIp);
  const code = codeRaw.trim();
  if (code.length !== 8) {
    await recordBindFailure(clientIp);
    throw new ApiError(400, "BIND_INVALID", "Invalid credential");
  }
  const codeHash = hashCredentialCode(code);
  const cred = await prisma.credential.findFirst({
    where: { codeHash },
  });
  if (!cred || cred.status !== CredentialStatus.ACTIVE) {
    await recordBindFailure(clientIp);
    throw new ApiError(400, "BIND_INVALID", "Invalid credential");
  }
  if (cred.expiresAt && cred.expiresAt < new Date()) {
    await prisma.credential.update({
      where: { id: cred.id },
      data: { status: CredentialStatus.EXPIRED },
    });
    await recordBindFailure(clientIp);
    throw new ApiError(400, "BIND_INVALID", "Invalid or expired credential");
  }
  const bind_challenge_token = await createBindChallenge(codeHash);
  return { bind_challenge_token };
}

/** Refresh channel token for an already-bound agent_user_id (no credential code needed). */
export async function refreshChannelToken(
  agentUserId: string,
): Promise<{ channel_token: string }> {
  const mapping = await prisma.agentIdentityMapping.findUnique({
    where: { agentUserId: agentUserId.trim() },
  });
  if (!mapping) {
    throw new ApiError(404, "BIND_NOT_FOUND", "No binding found for agent_user_id");
  }
  const channel_token = await signChannelToken({
    platform_user_id: mapping.platformUserId,
    agent_user_id: mapping.agentUserId,
    channel: mapping.channel,
  });
  return { channel_token };
}

/** Step 3: consume challenge and finalize bind + channel token. */
export async function completeBindCredential(
  bindChallengeToken: string,
  agentUserId: string,
  channel: string,
  clientIp: string,
): Promise<BindCredentialResponseDto> {
  await assertBindAttemptAllowed(clientIp);
  const codeHash = await consumeBindChallenge(bindChallengeToken);
  if (!codeHash) {
    await recordBindFailure(clientIp);
    throw new ApiError(400, "BIND_INVALID", "Invalid or expired bind challenge");
  }
  const txResult = await performBindTransaction(
    codeHash,
    agentUserId.trim(),
    channel.trim(),
  );
  if (!txResult.ok) {
    if (txResult.reason === "agent_user_bound_elsewhere") {
      throw new ApiError(
        409,
        "CONFLICT",
        "This agent_user_id is already bound to another platform account. " +
          "Use a new agent_user_id (edu_agent.yaml / clear identity) or bind with a code from that same account.",
      );
    }
    await recordBindFailure(clientIp);
    throw new ApiError(400, "BIND_INVALID", "Invalid or expired credential");
  }
  const channel_token = await signChannelToken({
    platform_user_id: txResult.platformUserId,
    agent_user_id: agentUserId.trim(),
    channel: channel.trim(),
  });
  return {
    success: true,
    platform_user_id: txResult.platformUserId,
    channel_token,
  };
}
