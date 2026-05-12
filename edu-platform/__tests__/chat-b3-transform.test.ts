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

function encodeAgentSse(parts: string[]): Uint8Array {
  const enc = new TextEncoder();
  return enc.encode(parts.join(""));
}

describe("createB3SseTransformFromAgent", () => {
  beforeEach(() => {
    createMock.mockReset();
  });

  it("maps Agent SSE to B3 text and done, persists when enabled", async () => {
    const input = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encodeAgentSse([
            'data: {"choices":[{"delta":{"content":"He"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{"content":"llo"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{}}],"edu_meta":{"content_type":"text","is_final":true,"b3":{"execution_time_ms":12,"model_used":"gpt-test","prompt_tokens":1,"completion_tokens":2,"total_tokens":3,"hit_chunks":["c1"],"hit_materials":["m1"],"hit_sources":["course"]}}}\n\n',
            "data: [DONE]\n\n",
          ]),
        );
        controller.close();
      },
    });

    const tr = createB3SseTransformFromAgent({
      courseId: "00000000-0000-4000-8000-0000000000aa",
      platformStudentId: "00000000-0000-4000-8000-0000000000bb",
      lessonId: null,
      sessionId: "sess-1",
      question: "Q?",
      answer: "",
      persist: true,
    });

    const text = await new Response(input.pipeThrough(tr)).text();
    expect(text).toContain('"type":"text"');
    expect(text).toContain('"type":"citation"');
    expect(text).toContain('"type":"done"');
    expect(text).toContain('"content":"He"');
    expect(text).toContain('"content":"llo"');
    expect(createMock).toHaveBeenCalledTimes(1);
    const arg = createMock.mock.calls[0]![0] as { data: { answer: string } };
    expect(arg.data.answer).toBe("Hello");
  });

  it("skips prisma when persist is false", async () => {
    const input = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encodeAgentSse([
            'data: {"choices":[{"delta":{"content":"x"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{}}],"edu_meta":{"content_type":"text","is_final":true,"b3":{"execution_time_ms":1,"model_used":"m","total_tokens":1,"hit_chunks":[],"hit_materials":[],"hit_sources":[]}}}\n\n',
            "data: [DONE]\n\n",
          ]),
        );
        controller.close();
      },
    });
    const tr = createB3SseTransformFromAgent({
      courseId: "00000000-0000-4000-8000-0000000000aa",
      platformStudentId: "00000000-0000-4000-8000-0000000000bb",
      lessonId: null,
      sessionId: "s",
      question: "q",
      answer: "",
      persist: false,
    });
    await new Response(input.pipeThrough(tr)).text();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("emits tool_call and tool_result B3 events and still persists answer", async () => {
    const input = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encodeAgentSse([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"knowledge_query","arguments":"{}"}}]}}],"edu_meta":{"content_type":"tool_call","is_final":false,"b3":{"tool_name":"knowledge_query"}}}\n\n',
            'data: {"choices":[{"delta":{"content":"Hi"}}],"edu_meta":{"content_type":"text","is_final":false}}\n\n',
            'data: {"choices":[{"delta":{"role":"tool","content":""}}],"edu_meta":{"content_type":"tool_result","is_final":false,"b3":{"tool_name":"knowledge_query","success":true,"duration_s":0.42}}}\n\n',
            'data: {"choices":[{"delta":{}}],"edu_meta":{"content_type":"text","is_final":true,"b3":{"execution_time_ms":20,"model_used":"gpt-test","total_tokens":5,"hit_chunks":[],"hit_materials":[],"hit_sources":[]}}}\n\n',
            "data: [DONE]\n\n",
          ]),
        );
        controller.close();
      },
    });

    const tr = createB3SseTransformFromAgent({
      courseId: "00000000-0000-4000-8000-0000000000aa",
      platformStudentId: "00000000-0000-4000-8000-0000000000bb",
      lessonId: null,
      sessionId: "sess-tool",
      question: "Q?",
      answer: "",
      persist: true,
    });

    const text = await new Response(input.pipeThrough(tr)).text();
    expect(text).toContain('"type":"tool_call"');
    expect(text).toContain('"name":"knowledge_query"');
    expect(text).toContain('"tool_call_id":"call_1"');
    expect(text).toContain('"type":"tool_result"');
    expect(text).toContain('"duration_ms":420');
    expect(text).toContain('"content":"Hi"');
    expect(createMock).toHaveBeenCalledTimes(1);
    const arg = createMock.mock.calls[0]![0] as { data: { answer: string } };
    expect(arg.data.answer).toBe("Hi");
  });
});
