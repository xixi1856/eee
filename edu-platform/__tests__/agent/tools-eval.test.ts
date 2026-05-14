import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { hintGeneratorTool, scoreEssayTool, evaluateCodeTool } from "@/lib/agent/tools/eval";

describe("eval tools", () => {
  beforeEach(() => {
    vi.stubEnv("RAG_SERVICE_URL", "http://rag.test");
    vi.stubEnv("RAG_SERVICE_API_KEY", "internal-key");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("业务规则：hint_generator 在问题为空时应拒绝执行", async () => {
    // given

    // when
    const result = await hintGeneratorTool.execute({ question: "   " }, {} as never);

    // then
    expect(result).toContain("缺少必要参数：question");
  });

  it("业务规则：hint_generator 成功时应返回评估服务的结果文本", async () => {
    // given
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: "先回顾拥塞窗口与慢启动的区别" }),
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    // when
    const result = await hintGeneratorTool.execute({ question: "拥塞控制不会" }, {} as never);

    // then
    expect(result).toContain("先回顾拥塞窗口");
    const [, req] = fetchMock.mock.calls[0] as [string, RequestInit];
    const payload = JSON.parse(String(req.body)) as Record<string, unknown>;
    expect(payload.eval_type).toBe("hint");
    expect(payload.level).toBe(1);
  });

  it("业务规则：score_essay 缺少题目或作答时应返回校验错误", async () => {
    // given

    // when
    const result = await scoreEssayTool.execute({ question: "", student_answer: "" }, {} as never);

    // then
    expect(result).toContain("缺少必要参数：question 或 student_answer");
  });

  it("业务规则：evaluate_code 在上游评估服务失败时应抛错", async () => {
    // given
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      text: async () => "service unavailable",
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    // when
    const action = evaluateCodeTool.execute(
      { code: "print(1)", task_description: "输出 1" },
      {} as never,
    );

    // then
    await expect(action).rejects.toThrow("RAG eval error 503");
  });
});
