import type { UserRole } from "@prisma/client";

export type RegisterBody = {
  username: string;
  email: string;
  password: string;
  role: UserRole;
};

export type LoginBody = {
  username: string;
  password: string;
};

export type RefreshBody = {
  refresh_token: string;
};

/** Authenticated user changes their own password (POST /api/v1/me/password). */
export type ChangePasswordBody = {
  current_password: string;
  new_password: string;
};

export type PublicUserDto = {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  real_name: string | null;
  avatar_url: string | null;
  /** B3: when false, chat answers are not persisted to ``qa_logs``. */
  qa_collection_enabled: boolean;
  qa_collection_notice_accepted_at: string | null;
};

/** Registration returns the new user. */
export type RegisterResponseDto = {
  user: PublicUserDto;
};

export type LoginResponseDto = {
  token: string;
  refresh_token: string;
  user: PublicUserDto;
};

export type RefreshResponseDto = {
  token: string;
  refresh_token: string;
};
