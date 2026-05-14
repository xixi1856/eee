import { describe, it, expect } from "vitest";
import { ContextManager, estimateTokens } from "@/lib/agent/context-manager";
import type { Message } from "@/lib/agent/types";

describe("ContextManager", () => {
  it("业务规则：在 token 未超限时应保留完整上下文", () => {
    // given
    const messages: Message[] = [
      { role: "system", content: "系统提示" },
      { role: "user", content: "什么是 TCP 三次握手？" },
      { role: "assistant", content: "我来分步解释。" },
    ];
    const manager = new ContextManager(1000);

    // when
    const compressed = manager.compress(messages);

    // then
    expect(compressed).toEqual(messages);
    expect(estimateTokens(compressed)).toBeLessThanOrEqual(1000);
  });

  it("业务规则：超限时应优先保留 system 与最近对话", () => {
    // given
    const messages: Message[] = [
      { role: "system", content: "你是教学助手" },
      { role: "user", content: "old-1".repeat(80) },
      { role: "assistant", content: "old-2".repeat(80) },
      { role: "user", content: "recent-question" },
      { role: "assistant", content: "recent-answer" },
    ];
    const manager = new ContextManager(60);

    // when
    const compressed = manager.compress(messages);

    // then
    expect(compressed[0]).toEqual(messages[0]);
    expect(compressed.some((m) => m.content.includes("recent-question"))).toBe(true);
    expect(compressed.some((m) => m.content.includes("recent-answer"))).toBe(true);
    expect(compressed.some((m) => m.content.includes("old-1"))).toBe(false);
  });

  it("业务规则：即使超限也至少保留最后两条业务消息", () => {
    // given
    const messages: Message[] = [
      { role: "user", content: "a".repeat(300) },
      { role: "assistant", content: "b".repeat(300) },
      { role: "user", content: "最后一个问题".repeat(200) },
      { role: "assistant", content: "最后一个回答".repeat(200) },
    ];
    const manager = new ContextManager(10);

    // when
    const compressed = manager.compress(messages);

    // then
    expect(compressed.slice(-2)).toEqual([
      { role: "user", content: "最后一个问题".repeat(200) },
      { role: "assistant", content: "最后一个回答".repeat(200) },
    ]);
  });

  it("业务规则：带 tool_calls 的消息应计入 token 预算", () => {
    // given
    const withToolCalls: Message[] = [
      {
        role: "assistant",
        content: "调用工具",
        tool_calls: [
          {
            id: "call_1",
            type: "function",
            function: { name: "knowledge_query", arguments: '{"question":"x"}' },
          },
        ],
      },
    ];
    const withoutToolCalls: Message[] = [{ role: "assistant", content: "调用工具" }];

    // when
    const a = estimateTokens(withToolCalls);
    const b = estimateTokens(withoutToolCalls);

    // then
    expect(a).toBeGreaterThan(b);
  });
});
