/**
 * ContextManager — token estimation and sliding-window compression.
 *
 * Uses a simple char-based approximation (1 token ≈ 4 chars for Chinese/English mix).
 * For production accuracy, replace with a WASM tiktoken binding.
 */

import type { Message } from "./types";

const CHARS_PER_TOKEN = 4;

function estimateTokensForText(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

export function estimateTokens(messages: Message[]): number {
  let total = 0;
  for (const m of messages) {
    total += 4; // per-message overhead
    total += estimateTokensForText(m.content);
    if (m.tool_calls) {
      total += estimateTokensForText(JSON.stringify(m.tool_calls));
    }
  }
  return total;
}

export class ContextManager {
  private maxTokens: number;

  constructor(maxTokens = 32_000) {
    this.maxTokens = maxTokens;
  }

  estimateTokens(messages: Message[]): number {
    return estimateTokens(messages);
  }

  /**
   * Compress history to fit within maxTokens.
   *
   * Strategy:
   *  1. Always keep the system message (index 0, if present).
   *  2. Always keep the last 6 messages (recent context).
   *  3. Drop oldest messages from the middle until we fit.
   *
   * This is a simple sliding-window; Phase 4+ can add LLM summarisation.
   */
  compress(messages: Message[], maxTokens?: number): Message[] {
    const limit = maxTokens ?? this.maxTokens;
    if (estimateTokens(messages) <= limit) return messages;

    const system = messages[0]?.role === "system" ? [messages[0]] : [];
    const rest = messages[0]?.role === "system" ? messages.slice(1) : messages;

    // Keep the last N messages that fit
    let kept: Message[] = [];
    let tokens = estimateTokens(system);
    for (let i = rest.length - 1; i >= 0; i--) {
      const t = estimateTokens([rest[i]]);
      if (tokens + t > limit) break;
      kept.unshift(rest[i]);
      tokens += t;
    }

    // Ensure at minimum the last 2 messages are kept (user + previous assistant)
    if (kept.length === 0 && rest.length > 0) {
      kept = rest.slice(-2);
    }

    return [...system, ...kept];
  }
}
