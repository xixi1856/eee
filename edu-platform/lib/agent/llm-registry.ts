/**
 * LLM Registry — role-based LLM client factory.
 *
 * Roles:
 *   chat    — main conversation & assignment generation (e.g. deepseek-v4-pro)
 *   vision  — image understanding (e.g. qwen3.6-plus)
 *   title   — chat title generation, cheap/fast (e.g. deepseek-v4-flash)
 *   memory  — memory extraction, auxiliary (e.g. qwen-plus / gpt-4o-mini)
 *
 * Fallback chains (first non-empty value wins):
 *
 *   chat   → key: LLM_CHAT_API_KEY  → LLM_API_KEY → OPENAI_API_KEY
 *            url: LLM_CHAT_BASE_URL → LLM_BASE_URL
 *          model: LLM_CHAT_MODEL    → LLM_MODEL → "gpt-4o"
 *
 *   title  → key: LLM_TITLE_API_KEY → LLM_CHAT_API_KEY → LLM_API_KEY → OPENAI_API_KEY
 *            url: LLM_TITLE_BASE_URL → LLM_CHAT_BASE_URL → LLM_BASE_URL
 *          model: LLM_TITLE_MODEL   → LLM_AUXILIARY_MODEL → LLM_MODEL → "gpt-4o-mini"
 *
 *   vision → key: LLM_VISION_API_KEY → LLM_API_KEY → OPENAI_API_KEY
 *            url: LLM_VISION_BASE_URL → LLM_BASE_URL
 *          model: LLM_VISION_MODEL  → LLM_MODEL → "gpt-4o"
 *
 *   memory → key: LLM_API_KEY → OPENAI_API_KEY
 *            url: LLM_BASE_URL
 *          model: LLM_AUXILIARY_MODEL → LLM_MODEL → "gpt-4o-mini"
 */

import OpenAI from "openai";

export type LLMRole = "chat" | "vision" | "title" | "memory";

export type RoleConfig = {
  apiKey: string;
  baseURL: string | undefined;
  model: string;
};

/** Return non-empty env var value or undefined. */
function e(name: string): string | undefined {
  const v = process.env[name];
  return v && v.trim() ? v.trim() : undefined;
}

export function getRoleConfig(role: LLMRole): RoleConfig {
  const defaultKey = e("LLM_API_KEY") ?? e("OPENAI_API_KEY") ?? "";
  const defaultBase = e("LLM_BASE_URL");
  const defaultModel = e("LLM_MODEL") ?? "gpt-4o";

  switch (role) {
    case "chat":
      return {
        apiKey: e("LLM_CHAT_API_KEY") ?? defaultKey,
        baseURL: e("LLM_CHAT_BASE_URL") ?? defaultBase,
        model: e("LLM_CHAT_MODEL") ?? defaultModel,
      };

    case "title":
      return {
        apiKey: e("LLM_TITLE_API_KEY") ?? e("LLM_CHAT_API_KEY") ?? defaultKey,
        baseURL: e("LLM_TITLE_BASE_URL") ?? e("LLM_CHAT_BASE_URL") ?? defaultBase,
        model: e("LLM_TITLE_MODEL") ?? e("LLM_AUXILIARY_MODEL") ?? e("LLM_MODEL") ?? "gpt-4o-mini",
      };

    case "vision":
      return {
        apiKey: e("LLM_VISION_API_KEY") ?? defaultKey,
        baseURL: e("LLM_VISION_BASE_URL") ?? defaultBase,
        model: e("LLM_VISION_MODEL") ?? defaultModel,
      };

    case "memory":
      return {
        apiKey: defaultKey,
        baseURL: defaultBase,
        model: e("LLM_AUXILIARY_MODEL") ?? e("LLM_MODEL") ?? "gpt-4o-mini",
      };
  }
}

/** Create a new OpenAI-compatible client for the given role. */
export function getLLMClient(role: LLMRole): OpenAI {
  const { apiKey, baseURL } = getRoleConfig(role);
  return new OpenAI({ apiKey, baseURL });
}

/**
 * Returns extra_body to disable DeepSeek thinking mode, preventing 400 errors
 * when tool calls are present (reasoning_content not echoed back).
 * Returns undefined for non-DeepSeek providers (Qwen, OpenAI, etc.).
 */
export function getRoleExtraBody(
  role: LLMRole,
): Record<string, unknown> | undefined {
  const { baseURL, model } = getRoleConfig(role);
  const isDeepSeek =
    (baseURL ?? "").includes("deepseek.com") ||
    model.toLowerCase().startsWith("deepseek");
  return isDeepSeek ? { thinking: { type: "disabled" } } : undefined;
}

export function getChatModel(): string {
  return getRoleConfig("chat").model;
}

export function getVisionModel(): string {
  return getRoleConfig("vision").model;
}

export function getTitleModel(): string {
  return getRoleConfig("title").model;
}

export function getMemoryModel(): string {
  return getRoleConfig("memory").model;
}
