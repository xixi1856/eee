/**
 * RAG tools: knowledge_query, generate_quiz, build_mindmap
 * All call the Python RAG microservice via HTTP.
 */

import type { Tool, TurnContext, ToolResult } from "../types";

// ---- Shared HTTP helper ----------------------------------------------------

async function ragPost<T>(
  url: string,
  key: string,
  body: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(key ? { "x-internal-key": key } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`RAG service error ${res.status}: ${text.slice(0, 400)}`);
  }
  return res.json() as Promise<T>;
}

// ---- Hit formatting --------------------------------------------------------

type HitItem = {
  chunk_id: string;
  text: string;
  origin: string;
  course_id?: string | null;
  material_id?: string | null;
  material_title?: string | null;
  relevance_score?: number;
  image_urls?: Array<{ page_idx: number; url: string }>;
};

function _formatHitsForLlm(hits: HitItem[]): string {
  if (hits.length === 0) return "（未找到相关内容）";
  return hits
    .map((h, i) => {
      const src = h.material_title ?? h.course_id ?? h.origin;
      return `[${i + 1}] 来源：${src}\n${h.text.slice(0, 1500)}`;
    })
    .join("\n\n---\n\n");
}

function _hitsToB3Citations(hits: HitItem[]): ToolResult["citations"] {
  return hits.map((h) => ({
    chunk_id: h.chunk_id,
    material_id: h.material_id ?? undefined,
    source_label: h.material_title ?? h.course_id ?? h.origin,
    chunk_text: h.text.slice(0, 300),
    image_urls: h.image_urls?.length ? h.image_urls : undefined,
  }));
}

// ---- knowledge_query -------------------------------------------------------

function _normalizeSource(raw: unknown): string | null {
  if (typeof raw === "string") {
    const s = raw.trim().toLowerCase();
    if (["personal", "course", "all", "enrolled_courses"].includes(s)) return s;
  }
  if (Array.isArray(raw)) {
    const items = (raw as unknown[])
      .filter((x) => typeof x === "string")
      .map((x) => (x as string).trim().toLowerCase());
    if (items.every((x) => ["course", "personal"].includes(x))) {
      const set = new Set(items);
      if (set.has("course") && set.has("personal")) return "all";
      if (set.has("course")) return "course";
      if (set.has("personal")) return "personal";
    }
  }
  return null;
}

export const knowledgeQueryTool: Tool = {
  name: "knowledge_query",
  description:
    "从知识库中检索信息，回答关于已导入文档/课程资料（如 PPT、PDF、讲义）的任何问题。" +
    "在回答概念、原理、定义、事实类问题时应首先调用此工具。",
  parameters: {
    type: "object",
    properties: {
      question: { type: "string", description: "要查询的自然语言问题" },
      mode: {
        type: "string",
        enum: ["hybrid", "local", "global", "naive"],
        description: "检索模式：hybrid（默认）、local、global、naive",
      },
      sources: {
        description:
          "必填。字符串：personal | course | all | enrolled_courses；或数组：[course, personal]。",
        oneOf: [
          { type: "string", enum: ["personal", "course", "all", "enrolled_courses"] },
          { type: "array", items: { type: "string" }, minItems: 1, maxItems: 2 },
        ],
      },
      top_k: { type: "integer", description: "返回最大片段数（默认 5，范围 1–20）" },
    },
    required: ["question", "sources"],
  },
  async execute(args: Record<string, unknown>, ctx: TurnContext): Promise<ToolResult> {
    const ragUrl = process.env.RAG_SERVICE_URL ?? "http://localhost:8001";
    const ragKey = process.env.RAG_SERVICE_API_KEY ?? "";

    const question = typeof args.question === "string" ? args.question.trim() : "";
    if (!question) {
      return { content: JSON.stringify({ error: "缺少必要参数：question" }) };
    }

    const source = _normalizeSource(args.sources);
    if (!source) {
      return {
        content: JSON.stringify({
          error: "非法 sources（仅允许 personal、course、all、enrolled_courses 或 [course, personal]）",
        }),
      };
    }

    if (ctx.courseId && source === "enrolled_courses") {
      return {
        content: JSON.stringify({
          error: "当前会话已绑定课程，禁止使用 sources=enrolled_courses",
        }),
      };
    }
    if (!ctx.courseId && (source === "course" || source === "all")) {
      return {
        content: JSON.stringify({
          error: "当前会话未绑定课程，仅允许使用 personal 或 enrolled_courses",
        }),
      };
    }

    const top_k = typeof args.top_k === "number" ? Math.max(1, Math.min(20, args.top_k)) : 5;

    type QueryResp = { hits: HitItem[]; warnings: string[] };
    const resp = await ragPost<QueryResp>(`${ragUrl}/rag/query`, ragKey, {
      source,
      user_id: ctx.userId,
      accessible_course_ids: ctx.accessibleCourseIds,
      course_id: ctx.courseId ?? null,
      question,
      mode: args.mode ?? "hybrid",
      top_k,
    });

    const content = _formatHitsForLlm(resp.hits);
    const citations = _hitsToB3Citations(resp.hits);
    return { content, citations };
  },
};

// ---- generate_quiz ---------------------------------------------------------

export const generateQuizTool: Tool = {
  name: "generate_quiz",
  description:
    "根据课程知识库生成练习题。当用户要求练习、做题、出题或测验时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      count: { type: "integer", description: "生成题目数量（默认 5，最多 20）" },
      question_type: {
        type: "string",
        enum: ["single_choice", "multi_choice", "fill_blank", "short_answer", "mixed"],
        description: "题型：单选、多选、填空、简答、混合（默认混合）",
      },
    },
    required: [],
  },
  async execute(args: Record<string, unknown>, ctx: TurnContext): Promise<string> {
    if (!ctx.courseId) {
      return JSON.stringify({ error: "generate_quiz 需要绑定课程（无课程上下文）" });
    }
    const ragUrl = process.env.RAG_SERVICE_URL ?? "http://localhost:8001";
    const ragKey = process.env.RAG_SERVICE_API_KEY ?? "";

    const count =
      typeof args.count === "number" ? Math.max(1, Math.min(20, args.count)) : 5;
    const question_type =
      typeof args.question_type === "string" ? args.question_type : "mixed";

    const result = await ragPost<Record<string, unknown>>(`${ragUrl}/rag/generate-quiz`, ragKey, {
      course_id: ctx.courseId,
      count,
      question_type,
    });
    return JSON.stringify(result);
  },
};

// ---- build_mindmap ---------------------------------------------------------

export const buildMindmapTool: Tool = {
  name: "build_mindmap",
  description:
    "根据指定的 Markdown 文件或目录生成思维导图 HTML 文件。" +
    "当用户要求生成思维导图、知识结构图、知识树时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      source: {
        type: "string",
        description: "Markdown 文件路径或包含 Markdown 文件的目录路径",
      },
      refine: {
        type: "boolean",
        description: "是否使用 LLM 精炼（输出更丰富，但速度较慢）",
      },
    },
    required: ["source"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const ragUrl = process.env.RAG_SERVICE_URL ?? "http://localhost:8001";
    const ragKey = process.env.RAG_SERVICE_API_KEY ?? "";

    const source = typeof args.source === "string" ? args.source : "";
    if (!source) return JSON.stringify({ error: "缺少必要参数：source" });

    type MindmapResp = { markdown: string; html: string };
    const resp = await ragPost<MindmapResp>(`${ragUrl}/rag/build-mindmap`, ragKey, {
      source,
      refine: args.refine ?? false,
    });
    return JSON.stringify({ markdown: resp.markdown.slice(0, 5000), html_length: resp.html.length });
  },
};
