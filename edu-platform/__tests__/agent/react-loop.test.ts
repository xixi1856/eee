import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ToolRegistry } from "@/lib/agent/tool-registry";
import type { ReactLoopOptions } from "@/lib/agent/react-loop";

const {
  createCompletionMock,
  redisSetMock,
  redisGetMock,
  redisDelMock,
} = vi.hoisted(() => ({
  createCompletionMock: vi.fn(),
  redisSetMock: vi.fn(),
  redisGetMock: vi.fn(),
  redisDelMock: vi.fn(),
}));

vi.mock("openai", () => ({
  default: class MockOpenAI {
    chat = { completions: { create: createCompletionMock } };
  },
}));

vi.mock("@/lib/redis", () => ({
  getRedis: vi.fn(async () => ({
    set: redisSetMock,
    get: redisGetMock,
    del: redisDelMock,
  })),
}));

import { createReActStream } from "@/lib/agent/react-loop";

function makeStream(chunks: Array<Record<string, unknown>>) {
  return {
    async *[Symbol.asyncIterator]() {
      for (const c of chunks) {
        yield c;
      }
    },
  };
}

async function readEvents(stream: ReadableStream<Uint8Array>): Promise<Array<Record<string, unknown>>> {
  const text = await new Response(stream).text();
  return text
    .split("\n\n")
    .map((b) => b.trim())
    .filter((b) => b.startsWith("data: "))
    .map((b) => JSON.parse(b.slice("data: ".length)) as Record<string, unknown>);
}

function makeOpts(toolRegistry: ToolRegistry, override?: Partial<ReactLoopOptions["config"]>): ReactLoopOptions {
  return {
    userMessage: "请回答问题",
    config: {
      model: "gpt-test",
      systemPrompt: "你是老师",
      maxIterations: 4,
      ragServiceUrl: "http://rag",
      ragServiceKey: "",
      maxContextTokens: 10000,
      ...override,
    },
    toolRegistry,
    ctx: {
      userId: "u-1",
      sessionId: "s-1",
      accessibleCourseIds: ["c-1"],
      courseId: "c-1",
    },
    coordinator: {
      shouldRunConsolidation: () => false,
      consolidateSession: vi.fn(),
    } as never,
    promptBuilder: {
      buildSystemPrompt: () => "SYSTEM",
    } as never,
    skills: [],
    profile: null,
    memoryBlock: "",
    history: [],
  };
}

describe("createReActStream", () => {
  beforeEach(() => {
    createCompletionMock.mockReset();
    redisSetMock.mockReset();
    redisGetMock.mockReset();
    redisDelMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("业务规则：主循环应输出文本、工具结果、引用与完成事件", async () => {
    // given
    const executeMock = vi.fn(async (args: Record<string, unknown>) => ({
      content: `工具结果:${String(args.question)}`,
      citations: [{ chunk_id: "ch-1", chunk_text: "证据片段" }],
    }));
    const toolRegistry = new ToolRegistry();
    toolRegistry.register({
      name: "knowledge_query",
      description: "查询",
      parameters: { type: "object", properties: {} },
      execute: executeMock,
    });

    createCompletionMock
      .mockResolvedValueOnce(
        makeStream([
          {
            choices: [{ delta: { content: "我先检索资料。" } }],
          },
          {
            choices: [
              {
                delta: {
                  tool_calls: [
                    {
                      index: 0,
                      id: "tc-1",
                      function: { name: "knowledge_query", arguments: '{"question":"TCP"}' },
                    },
                  ],
                },
              },
            ],
          },
          { choices: [{ delta: {} }], usage: { total_tokens: 12 } },
        ]),
      )
      .mockResolvedValueOnce(
        makeStream([
          { choices: [{ delta: { content: "结论：TCP 通过三次握手建立连接。" } }] },
          { choices: [{ delta: {} }], usage: { total_tokens: 21 } },
        ]),
      );

    // when
    const events = await readEvents(createReActStream(makeOpts(toolRegistry)));

    // then
    expect(events.some((e) => e.type === "text" && String(e.content).includes("我先检索"))).toBe(true);
    expect(events.some((e) => e.type === "tool_call" && e.name === "knowledge_query")).toBe(true);
    expect(events.some((e) => e.type === "tool_result" && e.success === true)).toBe(true);
    expect(events.some((e) => e.type === "citation" && e.chunk_id === "ch-1")).toBe(true);
    expect(events.some((e) => e.type === "done" && e.tokens === 21)).toBe(true);
    expect(executeMock).toHaveBeenCalledWith(
      { question: "TCP" },
      expect.objectContaining({ userId: "u-1", sessionId: "s-1" }),
    );
  });

  it("业务规则：工具不存在时应返回失败结果但仍完成回答流程", async () => {
    // given
    const toolRegistry = new ToolRegistry();
    createCompletionMock
      .mockResolvedValueOnce(
        makeStream([
          {
            choices: [
              {
                delta: {
                  tool_calls: [
                    {
                      index: 0,
                      id: "tc-miss",
                      function: { name: "missing_tool", arguments: "{}" },
                    },
                  ],
                },
              },
            ],
          },
        ]),
      )
      .mockResolvedValueOnce(
        makeStream([{ choices: [{ delta: { content: "我会给出无工具兜底答案。" } }] }]),
      );

    // when
    const events = await readEvents(createReActStream(makeOpts(toolRegistry)));

    // then
    expect(events.some((e) => e.type === "tool_result" && e.name === "missing_tool" && e.success === false)).toBe(true);
    expect(events.some((e) => e.type === "text" && String(e.content).includes("兜底答案"))).toBe(true);
    expect(events.some((e) => e.type === "done")).toBe(true);
  });

  it("业务规则：需要审批且用户拒绝时不得执行危险工具", async () => {
    // given
    vi.useFakeTimers();
    const dangerousExecute = vi.fn(async () => "should not run");
    const toolRegistry = new ToolRegistry();
    toolRegistry.register({
      name: "delegate_task",
      description: "危险委派",
      parameters: { type: "object", properties: {} },
      requiresApproval: true,
      execute: dangerousExecute,
    });

    redisSetMock.mockResolvedValue(undefined);
    redisGetMock.mockResolvedValue(JSON.stringify({ approved: false, userId: "u-1" }));

    createCompletionMock
      .mockResolvedValueOnce(
        makeStream([
          {
            choices: [
              {
                delta: {
                  tool_calls: [
                    {
                      index: 0,
                      id: "tc-approve",
                      function: { name: "delegate_task", arguments: '{"task":"x"}' },
                    },
                  ],
                },
              },
            ],
          },
        ]),
      )
      .mockResolvedValueOnce(
        makeStream([{ choices: [{ delta: { content: "已拒绝危险操作，提供安全答复。" } }] }]),
      );

    // when
    const reading = readEvents(createReActStream(makeOpts(toolRegistry, { approvalMode: "require_user" })));
    await vi.advanceTimersByTimeAsync(1000);
    const events = await reading;

    // then
    expect(events.some((e) => e.type === "require_approval" && e.tool_name === "delegate_task")).toBe(true);
    expect(events.some((e) => e.type === "approval_resolved" && e.approved === false)).toBe(true);
    expect(events.some((e) => e.type === "tool_result" && e.name === "delegate_task" && e.success === false)).toBe(true);
    expect(dangerousExecute).not.toHaveBeenCalled();
  });
});
