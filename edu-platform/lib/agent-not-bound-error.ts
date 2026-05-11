import { ApiError } from "@/lib/http/api-error";

const MSG =
  "当前登录尚未绑定 Edu Agent 身份。请先在 Agent 侧完成绑定（edu bind），并在本平台的「凭证」页（/credentials）使用凭证码完成关联后再使用 AI 聊天。";

/** Same check as chat routes: JWT lacks agent_user_id after bind refresh may be needed. */
export function agentNotBoundError(): ApiError {
  return new ApiError(400, "AGENT_NOT_BOUND", MSG);
}
