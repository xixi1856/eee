/**
 * Eval tools: hint_generator, score_essay, evaluate_code
 * All call the RAG service /rag/eval endpoint.
 */

import type { Tool, TurnContext } from "../types";

async function evalPost(
  evalType: string,
  params: Record<string, unknown>,
): Promise<string> {
  const ragUrl = process.env.RAG_SERVICE_URL ?? "http://localhost:8001";
  const ragKey = process.env.RAG_SERVICE_API_KEY ?? "";

  const res = await fetch(`${ragUrl}/rag/eval`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(ragKey ? { "x-internal-key": ragKey } : {}),
    },
    body: JSON.stringify({ eval_type: evalType, ...params }),
  });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`RAG eval error ${res.status}: ${t.slice(0, 400)}`);
  }
  const data = (await res.json()) as Record<string, unknown>;
  return typeof data.result === "string" ? data.result : JSON.stringify(data);
}

export const hintGeneratorTool: Tool = {
  name: "hint_generator",
  description:
    "为学习者遇到的问题生成苏格拉底式分级提示，引导思考而不直接给出答案。" +
    "当学习者表示卡住、需要提示或要求引导时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      question: { type: "string", description: "学习者遇到困难的问题或题目" },
      context: { type: "string", description: "与问题相关的背景信息（可选）" },
      level: {
        type: "integer",
        description: "提示等级：1（轻微引导）、2（部分方向）、3（接近答案）",
      },
    },
    required: ["question"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const question = typeof args.question === "string" ? args.question.trim() : "";
    if (!question) return JSON.stringify({ error: "缺少必要参数：question" });
    return evalPost("hint", {
      question,
      context: args.context ?? "",
      level: args.level ?? 1,
    });
  },
};

export const scoreEssayTool: Tool = {
  name: "score_essay",
  description:
    "对学习者的书面作答或论述题答案进行评分，给出得分和改进建议。" +
    "当学习者提交作答希望获得反馈时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      question: { type: "string", description: "原始题目或问题" },
      student_answer: { type: "string", description: "学习者的作答内容" },
      rubric: { type: "string", description: "评分标准（可选，为空时使用通用标准）" },
    },
    required: ["question", "student_answer"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const question = typeof args.question === "string" ? args.question.trim() : "";
    const answer = typeof args.student_answer === "string" ? args.student_answer.trim() : "";
    if (!question || !answer)
      return JSON.stringify({ error: "缺少必要参数：question 或 student_answer" });
    return evalPost("score_essay", {
      question,
      answer,
      reference: args.rubric ?? "",
    });
  },
};

export const evaluateCodeTool: Tool = {
  name: "evaluate_code",
  description:
    "评估学习者提交的代码，检查正确性、代码质量和边界情况，给出建设性反馈。" +
    "当学习者提交代码并希望获得代码审查或反馈时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      code: { type: "string", description: "学习者提交的代码" },
      task_description: { type: "string", description: "编程任务描述或要求" },
      language: { type: "string", description: "编程语言（默认 python）" },
    },
    required: ["code", "task_description"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const code = typeof args.code === "string" ? args.code.trim() : "";
    const task = typeof args.task_description === "string" ? args.task_description.trim() : "";
    if (!code || !task)
      return JSON.stringify({ error: "缺少必要参数：code 或 task_description" });
    return evalPost("evaluate_code", {
      answer: code,
      reference: task,
      language: args.language ?? "python",
    });
  },
};
