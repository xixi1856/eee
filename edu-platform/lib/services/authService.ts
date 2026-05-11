import { UserRole } from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import { hashPassword, verifyPassword } from "@/lib/password";
import {
  generateRefreshTokenPlain,
  hashRefreshToken,
  signAccessToken,
} from "@/lib/auth";
import { getRefreshTtlSec, getSelfCredentialMaxExpiresMinutes } from "@/lib/config";
import type {
  ChangePasswordBody,
  LoginBody,
  LoginResponseDto,
  RefreshBody,
  RefreshResponseDto,
  RegisterBody,
  RegisterResponseDto,
} from "@/lib/dto/auth.dto";
import { assertPasswordPolicy } from "@/lib/validation/password-policy";
import { toPublicUser } from "@/lib/services/userService";
import { allocateStudentCredentialInTransaction } from "@/lib/services/credentialService";

async function loadAgentUserIdForUser(
  platformUserId: string,
): Promise<string | undefined> {
  const m = await prisma.agentIdentityMapping.findUnique({
    where: { platformUserId },
    select: { agentUserId: true },
  });
  return m?.agentUserId;
}

export async function registerUser(
  body: RegisterBody,
): Promise<RegisterResponseDto> {
  if (body.role === UserRole.ADMIN) {
    throw new ApiError(403, "FORBIDDEN", "Cannot register as ADMIN");
  }
  assertPasswordPolicy(body.password);
  const passwordHash = await hashPassword(body.password);
  try {
    return await prisma.$transaction(async (tx) => {
      const user = await tx.user.create({
        data: {
          username: body.username.trim(),
          email: body.email.trim().toLowerCase(),
          passwordHash,
          role: body.role,
          isActive: true,
        },
      });
      let credential: RegisterResponseDto["credential"];
      if (body.role === UserRole.STUDENT) {
        const maxMin = getSelfCredentialMaxExpiresMinutes();
        const expiresAt = new Date(Date.now() + maxMin * 60 * 1000);
        credential = await allocateStudentCredentialInTransaction(
          tx,
          user.id,
          expiresAt,
        );
      }
      return { user: toPublicUser(user), credential };
    });
  } catch {
    throw new ApiError(
      409,
      "CONFLICT",
      "Username or email already registered",
    );
  }
}

export async function loginUser(body: LoginBody): Promise<LoginResponseDto> {
  const user = await prisma.user.findFirst({
    where: { username: body.username.trim(), isActive: true },
  });
  if (!user) {
    throw new ApiError(401, "UNAUTHORIZED", "Invalid username or password");
  }
  const ok = await verifyPassword(user.passwordHash, body.password);
  if (!ok) {
    throw new ApiError(401, "UNAUTHORIZED", "Invalid username or password");
  }
  const agentUserId = await loadAgentUserIdForUser(user.id);
  const token = await signAccessToken({
    sub: user.id,
    username: user.username,
    role: user.role,
    agent_user_id: agentUserId,
  });
  const refreshPlain = generateRefreshTokenPlain();
  const refreshHash = hashRefreshToken(refreshPlain);
  const exp = new Date(Date.now() + getRefreshTtlSec() * 1000);
  await prisma.refreshToken.create({
    data: {
      userId: user.id,
      tokenHash: refreshHash,
      expiresAt: exp,
    },
  });
  return {
    token,
    refresh_token: refreshPlain,
    user: toPublicUser(user),
  };
}

export async function refreshSession(
  body: RefreshBody,
): Promise<RefreshResponseDto> {
  const hash = hashRefreshToken(body.refresh_token.trim());
  const now = new Date();
  return await prisma.$transaction(async (tx) => {
    const row = await tx.refreshToken.findFirst({
      where: {
        tokenHash: hash,
        revokedAt: null,
        expiresAt: { gt: now },
      },
    });
    if (!row) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid or expired refresh token");
    }
    const revoked = await tx.refreshToken.updateMany({
      where: { id: row.id, revokedAt: null },
      data: { revokedAt: now },
    });
    if (revoked.count !== 1) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid or expired refresh token");
    }
    const user = await tx.user.findFirst({
      where: { id: row.userId, isActive: true },
    });
    if (!user) {
      throw new ApiError(401, "UNAUTHORIZED", "User no longer active");
    }
    const agentUserId = await loadAgentUserIdForUser(user.id);
    const token = await signAccessToken({
      sub: user.id,
      username: user.username,
      role: user.role,
      agent_user_id: agentUserId,
    });
    const refreshPlain = generateRefreshTokenPlain();
    const refreshHash = hashRefreshToken(refreshPlain);
    const exp = new Date(Date.now() + getRefreshTtlSec() * 1000);
    await tx.refreshToken.create({
      data: {
        userId: user.id,
        tokenHash: refreshHash,
        expiresAt: exp,
      },
    });
    return { token, refresh_token: refreshPlain };
  });
}

export async function changePassword(
  userId: string,
  body: ChangePasswordBody,
): Promise<void> {
  if (body.new_password === body.current_password) {
    throw new ApiError(
      400,
      "VALIDATION_ERROR",
      "New password must differ from current password",
    );
  }
  assertPasswordPolicy(body.new_password);

  await prisma.$transaction(async (tx) => {
    const user = await tx.user.findFirst({
      where: { id: userId, isActive: true },
      select: { id: true, passwordHash: true },
    });
    if (!user) {
      throw new ApiError(404, "NOT_FOUND", "User not found");
    }
    const ok = await verifyPassword(user.passwordHash, body.current_password);
    if (!ok) {
      throw new ApiError(401, "UNAUTHORIZED", "Invalid current password");
    }
    const passwordHash = await hashPassword(body.new_password);
    await tx.user.update({
      where: { id: userId },
      data: { passwordHash },
    });
    const now = new Date();
    await tx.refreshToken.updateMany({
      where: { userId, revokedAt: null },
      data: { revokedAt: now },
    });
  });
}
