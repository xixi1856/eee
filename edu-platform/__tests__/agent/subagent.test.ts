import { describe, it, expect, vi } from "vitest";
import { runSubAgent } from "@/lib/agent/subagent";
import type { Tool } from "@/lib/agent/types";

type MockCreate = ReturnType<typeof vi.fn>;

function buildClient(create: MockCreate): {
  chat: { completions: { create: MockCreate } };
} {
  return {
    chat: {
      completions: {
        create,
      },
    },
  };
}

describe("runSubAgent", () => {
  it("业务规则：子代理内禁止递归委派", async () => {
    // given
    const create = vi.fn();
    const client = buildClient(create);

    // when
    const result = await runSubAgent(client as never, "gpt-test", {
      task: "再次委派任务",
      allowedTools: [],
    }, 1);

    // then
    expect(result.success).toBe(false);
    expect(result.error).toContain("禁止递归委派");
  });

  it("业务规则：当模型直接给出最终回答时应返回该回答", async () => {
    // given
    const create = vi.fn().mockResolvedValue({
      choices: [{ message: { content: "子任务完成" }, finish_reason: "stop" }],
    });
    const client = buildClient(create);

    // when
    const result = await runSubAgent(client as never, "gpt-test", {
      task: "总结本章重点",
      allowedTools: [],
    });

    // then
    expect(result.success).toBe(true);
    expect(result.summary).toBe("子任务完成");
  });

  it("业务规则：工具执行失败时应继续完成任务并返回后续总结", async () => {
    // given
    const failingTool: Tool = {
      name: "calculate",
      description: "计算",
      parameters: { type: "object", properties: {} },
      execute: async () => {
        throw new Error("service unavailable");
      },
    };

    const create = vi
      .fn()
      .mockResolvedValueOnce({
        choices: [
          {
            message: {
              content: null,
              tool_calls: [
                {
                  id: "call_1",
                  type: "function",
                  function: { name: "calculate", arguments: '{"x": 1}' },
                },
              ],
            },
            finish_reason: "tool_calls",
          },
        ],
      })
      .mockResolvedValueOnce({
        choices: [{ message: { content: "已完成兜底处理" }, finish_reason: "stop" }],
      });

    const client = buildClient(create);

    // when
    const result = await runSubAgent(client as never, "gpt-test", {
      task: "做一个计算并给出结论",
      allowedTools: [failingTool],
      ctx: {
        userId: "u-1",
        sessionId: "s-1",
        accessibleCourseIds: [],
      },
    });

    // then
    expect(result.success).toBe(true);
    expect(result.summary).toBe("已完成兜底处理");
  });
});
