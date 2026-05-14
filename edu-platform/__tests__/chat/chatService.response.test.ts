import { describe, it, expect, vi, beforeEach } from "vitest";

const {
  userFindFirstMock,
  courseSessionFindUniqueMock,
  courseSessionCreateMock,
  qaCenterFindFirstMock,
  qaCenterCreateMock,
  qaLogCreateMock,
  createReActStreamMock,
  sessionGetMock,
  sessionSetMock,
  memoryBlockMock,
  loadSkillsMock,
  loadProfileMock,
} = vi.hoisted(() => ({
  userFindFirstMock: vi.fn(),
  courseSessionFindUniqueMock: vi.fn(),
  courseSessionCreateMock: vi.fn(),
  qaCenterFindFirstMock: vi.fn(),
  qaCenterCreateMock: vi.fn(),
  qaLogCreateMock: vi.fn(),
  createReActStreamMock: vi.fn(),
  sessionGetMock: vi.fn(),
  sessionSetMock: vi.fn(),
  memoryBlockMock: vi.fn(),
  loadSkillsMock: vi.fn(),
  loadProfileMock: vi.fn(),
}));

vi.mock("crypto", () => ({
  randomUUID: vi.fn(() => "qa-sess-fixed"),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    user: { findFirst: userFindFirstMock },
    courseChatSession: {
      findUnique: courseSessionFindUniqueMock,
      create: courseSessionCreateMock,
    },
    qaCenterSession: {
      findFirst: qaCenterFindFirstMock,
      create: qaCenterCreateMock,
    },
    qaLog: { create: qaLogCreateMock },
  },
}));

vi.mock("@/lib/agent/react-loop", () => ({
  createReActStream: createReActStreamMock,
}));

vi.mock("@/lib/agent/session-store", () => ({
  sessionStore: {
    get: sessionGetMock,
    set: sessionSetMock,
  },
}));

vi.mock("@/lib/agent/setup", () => ({
  getMemoryCoordinator: vi.fn(() => ({
    buildRetrievedMemoryBlock: memoryBlockMock,
    shouldRunConsolidation: vi.fn(() => false),
    consolidateSession: vi.fn(),
  })),
  getSkillsLoader: vi.fn(() => ({
    load: loadSkillsMock,
  })),
  buildAgentConfig: vi.fn(() => ({
    model: "gpt-test",
    systemPrompt: "sys",
    maxIterations: 4,
    ragServiceUrl: "http://rag",
    ragServiceKey: "",
    maxContextTokens: 120000,
  })),
}));

vi.mock("@/lib/agent/memory/memory-store", () => ({
  memoryStore: {
    loadProfile: loadProfileMock,
  },
}));

function makeAgentStream(events: Array<Record<string, unknown>>): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const ev of events) {
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`));
      }
      controller.close();
    },
  });
}

import { courseChatSseResponse, qaCenterChatSseResponse } from "@/lib/services/chatService";

describe("chatService response integration", () => {
  beforeEach(() => {
    userFindFirstMock.mockReset();
    courseSessionFindUniqueMock.mockReset();
    courseSessionCreateMock.mockReset();
    qaCenterFindFirstMock.mockReset();
    qaCenterCreateMock.mockReset();
    qaLogCreateMock.mockReset();
    createReActStreamMock.mockReset();
    sessionGetMock.mockReset();
    sessionSetMock.mockReset();
    memoryBlockMock.mockReset();
    loadSkillsMock.mockReset();
    loadProfileMock.mockReset();

    memoryBlockMock.mockResolvedValue("");
    loadSkillsMock.mockReturnValue([]);
    loadProfileMock.mockResolvedValue(null);
    sessionGetMock.mockResolvedValue([]);
    sessionSetMock.mockResolvedValue(undefined);
    qaLogCreateMock.mockResolvedValue(undefined);
  });

  it("业务规则：课程对话应输出 SSE 并持久化问答与会话历史", async () => {
    // given
    userFindFirstMock.mockResolvedValue({ qaCollectionEnabled: true });
    courseSessionFindUniqueMock.mockResolvedValue({ agentSessionId: "course-sess-1" });
    sessionGetMock.mockResolvedValue([{ role: "user", content: "历史问题" }]);

    createReActStreamMock.mockReturnValue(
      makeAgentStream([
        { type: "text", content: "回答A" },
        { type: "done", tokens: 8, exec_time_ms: 20 },
      ]),
    );

    // when
    const resp = await courseChatSseResponse({
      courseId: "c-1",
      platformStudentId: "u-1",
      userId: "u-1",
      message: "本次问题",
      accessibleCourseIds: ["c-1"],
      lessonId: null,
    });
    const body = await resp.text();

    // then
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toContain("text/event-stream");
    expect(body).toContain('"type":"text"');
    expect(body).toContain("回答A");

    const qaData = (qaLogCreateMock.mock.calls[0]![0] as { data: Record<string, unknown> }).data;
    expect(qaData.question).toBe("本次问题");
    expect(qaData.answer).toBe("回答A");
    expect(qaData.sessionId).toBe("course-sess-1");

    const [savedSessionId, savedMessages] = sessionSetMock.mock.calls[0] as [
      string,
      Array<{ role: string; content: string }>,
    ];
    expect(savedSessionId).toBe("course-sess-1");
    expect(savedMessages.slice(-2)).toEqual([
      { role: "user", content: "本次问题" },
      { role: "assistant", content: "回答A" },
    ]);
  });

  it("业务规则：问答中心无会话 ID 时应创建新会话并返回响应头", async () => {
    // given
    userFindFirstMock.mockResolvedValue({ qaCollectionEnabled: false });
    createReActStreamMock.mockReturnValue(
      makeAgentStream([
        { type: "text", content: "跨课程回答" },
        { type: "done", tokens: 5, exec_time_ms: 11 },
      ]),
    );

    // when
    const resp = await qaCenterChatSseResponse({
      platformStudentId: "u-1",
      userId: "u-1",
      message: "问答中心问题",
      accessibleCourseIds: ["c-1", "c-2"],
      sessionId: null,
    });
    const body = await resp.text();

    // then
    expect(resp.status).toBe(200);
    expect(resp.headers.get("X-Qa-Center-Session-Id")).toBe("qa-sess-fixed");
    expect(body).toContain("跨课程回答");
    expect(qaCenterCreateMock).toHaveBeenCalledWith({
      data: {
        studentId: "u-1",
        agentSessionId: "qa-sess-fixed",
      },
    });
    expect(qaLogCreateMock).not.toHaveBeenCalled();

    const [savedSessionId] = sessionSetMock.mock.calls[0] as [string, Array<{ role: string; content: string }>];
    expect(savedSessionId).toBe("qa-sess-fixed");
  });
});
