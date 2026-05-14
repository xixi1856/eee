import { describe, it, expect, vi, beforeEach } from "vitest";
import type { TurnContext } from "@/lib/agent/types";

const {
  factCreateMock,
  factFindManyMock,
  conceptFindManyMock,
} = vi.hoisted(() => ({
  factCreateMock: vi.fn(),
  factFindManyMock: vi.fn(),
  conceptFindManyMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    userMemoryFact: {
      create: factCreateMock,
      findMany: factFindManyMock,
    },
    userMemoryConcept: {
      findMany: conceptFindManyMock,
    },
  },
}));

import { rememberFactTool, searchMemoryTool } from "@/lib/agent/tools/memory";

const ctx: TurnContext = {
  userId: "u-100",
  sessionId: "sess-100",
  accessibleCourseIds: [],
};

describe("memory tools", () => {
  beforeEach(() => {
    factCreateMock.mockReset();
    factFindManyMock.mockReset();
    conceptFindManyMock.mockReset();
  });

  it("业务规则：remember_fact 的 fact_content 不能为空", async () => {
    // given

    // when
    const result = await rememberFactTool.execute({ fact_content: "  " }, ctx);

    // then
    expect(result).toContain("fact_content 不能为空");
  });

  it("业务规则：remember_fact 应写入用户维度事实并归一化置信度", async () => {
    // given
    factCreateMock.mockResolvedValue({});

    // when
    const result = await rememberFactTool.execute(
      {
        fact_content: "学习者更偏好图示解释",
        category: "invalid",
        confidence: 99,
      },
      ctx,
    );

    // then
    expect(result).toContain('"ok":true');
    const [payload] = factCreateMock.mock.calls[0] as [{ data: Record<string, unknown> }];
    const data = payload.data;
    expect(data.userId).toBe("u-100");
    expect(data.sessionId).toBe("sess-100");
    expect(data.category).toBe("preference");
    expect(data.confidence).toBe(1);
    expect(String(data.content)).toContain("偏好图示解释");
  });

  it("业务规则：search_memory 应返回概念与事实的语义匹配结果", async () => {
    // given
    factFindManyMock.mockResolvedValue([
      {
        id: "f-1",
        userId: "u-100",
        timestamp: new Date("2026-01-01"),
        category: "concept_confusion",
        content: "对 TCP 拥塞控制仍有困惑",
      },
    ]);
    conceptFindManyMock.mockResolvedValue([
      {
        id: "c-1",
        userId: "u-100",
        name: "TCP",
        description: "传输层可靠连接协议",
        masteryLevel: 0.42,
      },
    ]);

    // when
    const result = await searchMemoryTool.execute({ keyword: "tcp", limit: 5 }, ctx);

    // then
    expect(result).toContain("[概念] TCP（掌握度 0.42）");
    expect(result).toContain("[concept_confusion] 对 TCP 拥塞控制仍有困惑");
  });

  it("业务规则：search_memory keyword 为空时应返回输入校验错误", async () => {
    // given

    // when
    const result = await searchMemoryTool.execute({ keyword: "" }, ctx);

    // then
    expect(result).toContain("keyword 不能为空");
  });

  it("业务规则：remember_fact 写入失败时应向上抛出异常供上层处理", async () => {
    // given
    factCreateMock.mockRejectedValue(new Error("db down"));

    // when
    const action = rememberFactTool.execute({ fact_content: "A" }, ctx);

    // then
    await expect(action).rejects.toThrow("db down");
  });
});
