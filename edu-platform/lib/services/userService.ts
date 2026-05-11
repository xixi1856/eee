import type { Prisma, User } from "@prisma/client";
import { prisma } from "@/lib/db";
import { ApiError } from "@/lib/http/api-error";
import type { PublicUserDto } from "@/lib/dto/auth.dto";
import type { UpdateUserBody } from "@/lib/dto/user.dto";

export function toPublicUser(user: User): PublicUserDto {
  return {
    id: user.id,
    username: user.username,
    email: user.email,
    role: user.role,
    real_name: user.realName,
    avatar_url: user.avatarUrl,
    qa_collection_enabled: user.qaCollectionEnabled,
    qa_collection_notice_accepted_at:
      user.qaCollectionNoticeAcceptedAt?.toISOString() ?? null,
  };
}

export async function getUserProfile(userId: string): Promise<PublicUserDto> {
  const user = await prisma.user.findFirst({
    where: { id: userId, isActive: true },
  });
  if (!user) {
    throw new ApiError(404, "NOT_FOUND", "User not found");
  }
  return toPublicUser(user);
}

export async function updateUserProfile(
  userId: string,
  body: UpdateUserBody,
): Promise<PublicUserDto> {
  const data: Prisma.UserUpdateInput = {};
  if (body.real_name !== undefined) data.realName = body.real_name;
  if (body.avatar_url !== undefined) data.avatarUrl = body.avatar_url;
  if (body.email !== undefined) data.email = body.email;
  if (body.qa_collection_enabled !== undefined) {
    data.qaCollectionEnabled = body.qa_collection_enabled;
  }
  if (body.qa_collection_notice_accepted === true) {
    data.qaCollectionNoticeAcceptedAt = new Date();
  }

  if (Object.keys(data).length === 0) {
    return getUserProfile(userId);
  }

  try {
    const user = await prisma.user.update({
      where: { id: userId, isActive: true },
      data,
    });
    return toPublicUser(user);
  } catch {
    throw new ApiError(409, "CONFLICT", "Email may already be in use");
  }
}
