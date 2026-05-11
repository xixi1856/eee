import type { CredentialStatus } from "@prisma/client";

export type CreateCredentialBody = {
  expires_in_minutes?: number;
};

export type AdminCreateCredentialBody = {
  user_id: string;
  expires_in_minutes?: number;
};

export type CredentialCreatedDto = {
  code: string;
  expires_at: string | null;
  status: CredentialStatus;
  user_id?: string;
};

export type CredentialListItemDto = {
  id: string;
  user_id: string;
  status: CredentialStatus;
  created_at: string;
  expires_at: string | null;
  used_at: string | null;
  bound_at: string | null;
  bound_agent_user_id: string | null;
};

export type BindCredentialBody = {
  code: string;
  agent_user_id: string;
  channel: string;
};

export type BindCredentialResponseDto = {
  success: true;
  platform_user_id: string;
  channel_token: string;
};
