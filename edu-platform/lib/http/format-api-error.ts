const KNOWN_MESSAGE_ZH: Record<string, string> = {
  "Unsupported file type": "不支持的文件类型",
  "Empty file": "文件为空",
  "File too large": "文件过大",
  "multipart field 'file' is required": "请选择要上传的文件",
  "Object storage is not configured": "对象存储未配置，无法上传",
  "Object storage upload failed": "对象存储上传失败",
  "REDIS_URL is required for material processing": "材料处理服务未就绪（缺少 Redis 配置）",
};

/**
 * Turn a JSON API error body (from `ApiError.toBody()` / `jsonError`) into a single user-facing string.
 */
export function formatApiErrorFromResponse(
  status: number,
  responseText: string,
): string {
  if (!responseText?.trim()) {
    return `上传失败（HTTP ${status}）`;
  }
  try {
    const data = JSON.parse(responseText) as {
      error?: {
        message?: string;
        code?: string;
        details?: Record<string, unknown>;
      };
    };
    const err = data?.error;
    if (err?.message) {
      let msg = KNOWN_MESSAGE_ZH[err.message] ?? err.message;
      const details = err.details ?? {};
      const allowed = details.allowed;
      if (Array.isArray(allowed) && allowed.length) {
        msg += `（支持：${allowed.map(String).join("、")}）`;
      }
      const maxBytes = details.max_bytes;
      if (typeof maxBytes === "number" && Number.isFinite(maxBytes)) {
        const mb = maxBytes / (1024 * 1024);
        msg += `（单文件最大约 ${mb >= 1 ? mb.toFixed(0) : mb.toFixed(1)} MB）`;
      }
      return msg;
    }
  } catch {
    /* not JSON */
  }
  return `上传失败（HTTP ${status}）`;
}
