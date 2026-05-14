import { describe, it, expect, vi, beforeEach } from "vitest";

const { createMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
}));

vi.mock("@/lib/db", () => ({
  prisma: {
    qaLog: {
      create: createMock,
    },
  },
}));

import { createB3SseTransformFromAgent } from "@/lib/services/chatService";

function encode(parts: string[]): Uint8Array {
  return new TextEncoder().encode(parts.join(""));
}

describe("createB3SseTransformFromAgent edge cases", () => {
  beforeEach(() => {
    createMock.mockReset();
  });

  it("业务规则：上游流不完整时应向前端明确返回 STREAM_INCOMPLETE", async () => {
    // given
    const input = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encode([
            'data: {"choices":[{"delta":{"content":"partial"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
          ]),
        );
        controller.close();
      },
    });

    const tr = createB3SseTransformFromAgent({
      courseId: null,
      platformStudentId: "u-1",
      lessonId: null,
      sessionId: "s-incomplete",
      question: "Q",
      answer: "",
      persist: true,
    });

    // when
    const text = await new Response(input.pipeThrough(tr)).text();

    // then
    expect(text).toContain('"type":"done"');
    expect(text).toContain('"error":"STREAM_INCOMPLETE"');
    expect(createMock).not.toHaveBeenCalled();
  });

  it("业务规则：出现未匹配的 tool_call 也要记录到持久化日志", async () => {
    // given
    const input = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encode([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tc-1","type":"function","function":{"name":"knowledge_query","arguments":"{}"}}]}}],"edu_meta":{"content_type":"tool_call","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{"content":"ok"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{}}],"edu_meta":{"content_type":"text","is_final":true,"b3":{"execution_time_ms":9,"model_used":"gpt-test","total_tokens":4,"hit_chunks":[],"hit_materials":[],"hit_sources":[]}}}\n\n',
            "data: [DONE]\n\n",
          ]),
        );
        controller.close();
      },
    });

    const tr = createB3SseTransformFromAgent({
      courseId: "c-1",
      platformStudentId: "u-1",
      lessonId: null,
      sessionId: "s-unmatched",
      question: "Q",
      answer: "",
      persist: true,
    });

    // when
    const text = await new Response(input.pipeThrough(tr)).text();

    // then
    expect(text).toContain('"type":"tool_call"');
    expect(text).toContain('"name":"knowledge_query"');
    expect(createMock).toHaveBeenCalledTimes(1);
    const arg = createMock.mock.calls[0]![0] as { data: { toolCalls: Array<{ name: string; status: string }> } };
    expect(arg.data.toolCalls).toContainEqual({ name: "knowledge_query", status: "done" });
  });
});
