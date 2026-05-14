import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { knowledgeQueryTool, generateQuizTool, buildMindmapTool } from "@/lib/agent/tools/rag";
import type { TurnContext, ToolResult } from "@/lib/agent/types";

const ctx: TurnContext = {
  userId: "u-1",
  sessionId: "s-1",
  accessibleCourseIds: ["c-1", "c-2"],
  courseId: "c-1",
};

function asToolResult(value: string | ToolResult): ToolResult {
  if (typeof value === "string") {
    throw new Error("Expected ToolResult but received string");
  }
  return value;
}

function asString(value: string | ToolResult): string {
  if (typeof value !== "string") {
    throw new Error("Expected string but received ToolResult");
  }
  return value;
}

describe("RAG tools", () => {
  beforeEach(() => {
    vi.stubEnv("RAG_SERVICE_URL", "http://rag.test");
    vi.stubEnv("RAG_SERVICE_API_KEY", "internal-key");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("业务规则：knowledge_query 缺少 question 时应返回可读错误", async () => {
    // given

    // when
    const result = asToolResult(await knowledgeQueryTool.execute({ sources: "course" }, ctx));

    // then
    expect(result.content).toContain("缺少必要参数：question");
  });

  it("业务规则：knowledge_query 对非法 sources 应拒绝并返回契约错误", async () => {
    // given

    // when
    const result = asToolResult(await knowledgeQueryTool.execute(
      { question: "什么是 TCP", sources: "invalid-source" },
      ctx,
    ));

    // then
    expect(result.content).toContain("非法 sources");
  });

  it("业务规则：knowledge_query 应把课程+个人范围聚合为 all 并生成引用", async () => {
    // given
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        hits: [
          {
            chunk_id: "chunk-1",
            text: "TCP 通过三次握手建立可靠连接。",
            origin: "course",
            course_id: "c-1",
            material_id: "m-1",
            material_title: "网络基础",
          },
        ],
        warnings: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    // when
    const result = asToolResult(await knowledgeQueryTool.execute(
      {
        question: "TCP 如何建立连接？",
        sources: ["course", "personal"],
        top_k: 50,
      },
      ctx,
    ));

    // then
    expect(result.content).toContain("来源：网络基础");
    expect(result.content).toContain("TCP 通过三次握手建立可靠连接");
    expect(result.citations?.[0]).toMatchObject({
      chunk_id: "chunk-1",
      material_id: "m-1",
      source_label: "网络基础",
    });
    const [, req] = fetchMock.mock.calls[0] as [string, RequestInit];
    const payload = JSON.parse(String(req.body)) as Record<string, unknown>;
    expect(payload.source).toBe("all");
    expect(payload.top_k).toBe(20);
  });

  it("业务规则：generate_quiz 在无课程上下文时应拒绝执行", async () => {
    // given
    const noCourseCtx: TurnContext = {
      userId: "u-1",
      sessionId: "s-1",
      accessibleCourseIds: [],
      courseId: null,
    };

    // when
    const result = asString(await generateQuizTool.execute({ count: 3 }, noCourseCtx));

    // then
    expect(result).toContain("需要绑定课程");
  });

  it("业务规则：build_mindmap 成功时应返回摘要而非超长 HTML 正文", async () => {
    // given
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        markdown: "# 导图\n- A\n- B",
        html: "<html>" + "x".repeat(300) + "</html>",
      }),
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    // when
    const result = asString(await buildMindmapTool.execute({ source: "./notes" }, ctx));

    // then
    const parsed = JSON.parse(result) as { markdown: string; html_length: number };
    expect(parsed.markdown).toContain("# 导图");
    expect(parsed.html_length).toBeGreaterThan(100);
  });
});
