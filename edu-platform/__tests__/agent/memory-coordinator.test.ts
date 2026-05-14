import { describe, it, expect, vi } from "vitest";
import { MemoryCoordinator } from "@/lib/agent/memory/memory-coordinator";
import type { Message } from "@/lib/agent/types";

describe("MemoryCoordinator", () => {
  it("业务规则：用户提示为空时不应注入记忆块", async () => {
    // given
    const retriever = { getRelevantConcepts: vi.fn() };
    const consolidator = { consolidateSession: vi.fn() };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);

    // when
    const block = await coordinator.buildRetrievedMemoryBlock("u-1", "   ");

    // then
    expect(block).toBe("");
    expect(retriever.getRelevantConcepts).not.toHaveBeenCalled();
  });

  it("业务规则：检索到概念时应生成可读记忆块并按长度截断", async () => {
    // given
    const retriever = {
      getRelevantConcepts: vi.fn(async () => [
        { name: "TCP", masteryLevel: 0.8 },
        { name: "拥塞控制", masteryLevel: 0.3 },
        { name: "超长概念", masteryLevel: 0.5 },
      ]),
    };
    const consolidator = { consolidateSession: vi.fn() };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);

    // when
    const block = await coordinator.buildRetrievedMemoryBlock("u-1", "复习传输层");

    // then
    expect(block).toContain("TCP（掌握度 0.80）");
    expect(block).toContain("拥塞控制（掌握度 0.30）");
    expect(block.length).toBeLessThanOrEqual(1201);
  });

  it("业务规则：记忆检索失败时不应中断主流程，应返回空字符串", async () => {
    // given
    const retriever = {
      getRelevantConcepts: vi.fn(async () => {
        throw new Error("retriever down");
      }),
    };
    const consolidator = { consolidateSession: vi.fn() };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);

    // when
    const block = await coordinator.buildRetrievedMemoryBlock("u-1", "传输层");

    // then
    expect(block).toBe("");
  });

  it("业务规则：会话估算 token 达阈值后才触发 consolidation", () => {
    // given
    const retriever = { getRelevantConcepts: vi.fn() };
    const consolidator = { consolidateSession: vi.fn() };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);
    const shortMessages: Message[] = [{ role: "user", content: "短消息" }];
    const longMessages: Message[] = [
      { role: "user", content: "x".repeat(6000) },
      { role: "assistant", content: "y".repeat(6000) },
    ];

    // when
    const shouldShort = coordinator.shouldRunConsolidation(shortMessages);
    const shouldLong = coordinator.shouldRunConsolidation(longMessages);

    // then
    expect(shouldShort).toBe(false);
    expect(shouldLong).toBe(true);
  });

  it("业务规则：提供 knownTokens 时应使用真实 token 数而非估算值", () => {
    // given
    const retriever = { getRelevantConcepts: vi.fn() };
    const consolidator = { consolidateSession: vi.fn() };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);
    // 消息本身估算 token 低于阈值（内容很短）
    const shortMessages: Message[] = [
      { role: "user", content: "短消息" },
      { role: "assistant", content: "短回答" },
    ];

    // when / then
    // A: knownTokens 高于 800 → 触发（即便估算值低于阈值）
    expect(coordinator.shouldRunConsolidation(shortMessages, 1000)).toBe(true);
    // B: knownTokens 低于 800 → 不触发（即便内容再长也用真实值）
    const longMessages: Message[] = [
      { role: "user", content: "x".repeat(6000) },
      { role: "assistant", content: "y".repeat(6000) },
    ];
    expect(coordinator.shouldRunConsolidation(longMessages, 500)).toBe(false);
    // C: knownTokens 为 null → 回落到估算值
    expect(coordinator.shouldRunConsolidation(longMessages, null)).toBe(true);
    expect(coordinator.shouldRunConsolidation(shortMessages, null)).toBe(false);
  });

  it("业务规则：consolidation 失败不应向用户层抛错", async () => {
    // given
    const retriever = { getRelevantConcepts: vi.fn() };
    const consolidator = {
      consolidateSession: vi.fn(async () => {
        throw new Error("llm timeout");
      }),
    };
    const coordinator = new MemoryCoordinator(retriever as never, consolidator as never);

    // when
    const action = coordinator.consolidateSession("u-1", "s-1", [
      { role: "user", content: "Q" },
      { role: "assistant", content: "A" },
    ]);

    // then
    await expect(action).resolves.toBeUndefined();
  });
});
