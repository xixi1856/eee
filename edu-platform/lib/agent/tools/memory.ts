/**
 * Memory tools: remember_fact, search_memory
 * Write directly to Prisma (same tables as Python memory system).
 */

import { randomUUID } from "crypto";
import { prisma } from "@/lib/db";
import type { Tool, TurnContext } from "../types";

const VALID_CATEGORIES = new Set([
  "concept_mastery",
  "concept_confusion",
  "preference",
  "difficulty",
  "question",
  "achievement",
]);

export const rememberFactTool: Tool = {
  name: "remember_fact",
  description:
    "将当前对话中发现的重要学习事实记录到长期记忆中（如掌握情况、偏好、困惑点）。",
  requiresApproval: true,
  approvalReason: "此操作将在您的长期记忆中写入一条新记录，后续对话中 AI 会持续引用它。",
  category: "write",
  parameters: {
    type: "object",
    properties: {
      fact_content: { type: "string", description: "要记录的事实内容（简洁，50 字以内）" },
      category: {
        type: "string",
        enum: [
          "concept_mastery",
          "concept_confusion",
          "preference",
          "difficulty",
          "question",
          "achievement",
        ],
        description: "事实类别（默认 preference）",
      },
      confidence: {
        type: "number",
        description: "置信度 0–1（默认 0.85）",
      },
    },
    required: ["fact_content"],
  },
  async execute(args: Record<string, unknown>, ctx: TurnContext): Promise<string> {
    const content =
      typeof args.fact_content === "string" ? args.fact_content.trim() : "";
    if (!content) return JSON.stringify({ error: "fact_content 不能为空" });

    const cat =
      typeof args.category === "string" && VALID_CATEGORIES.has(args.category)
        ? args.category
        : "preference";

    let conf = 0.85;
    if (typeof args.confidence === "number") {
      conf = Math.max(0, Math.min(1, args.confidence));
    }

    const id = randomUUID();
    await prisma.userMemoryFact.create({
      data: {
        id,
        userId: ctx.userId,
        sessionId: ctx.sessionId,
        category: cat,
        content: content.slice(0, 500),
        confidence: conf,
        sourceJson: { session_id: ctx.sessionId, tool_name: "remember_fact" },
        metadata: { origin: "tool:remember_fact" },
      },
    });

    return JSON.stringify({ ok: true, id: id.slice(0, 8) + "…" });
  },
};

export const searchMemoryTool: Tool = {
  name: "search_memory",
  description:
    "从学习者的长期记忆中检索相关事实和概念（关键词匹配）。" +
    "当需要了解学习者过去的掌握情况、偏好或困惑时使用。",
  parameters: {
    type: "object",
    properties: {
      keyword: { type: "string", description: "搜索关键词" },
      limit: { type: "integer", description: "返回最大条数（默认 10）" },
    },
    required: ["keyword"],
  },
  async execute(args: Record<string, unknown>, ctx: TurnContext): Promise<string> {
    const kw =
      typeof args.keyword === "string" ? args.keyword.trim().toLowerCase() : "";
    if (!kw) return JSON.stringify({ error: "keyword 不能为空" });

    const limit =
      typeof args.limit === "number" ? Math.max(1, Math.min(30, args.limit)) : 10;

    const facts = await prisma.userMemoryFact.findMany({
      where: { userId: ctx.userId },
      orderBy: { timestamp: "desc" },
      take: 200, // fetch recent 200 then filter in-memory
    });

    const matched = facts
      .filter((f) => f.content.toLowerCase().includes(kw))
      .slice(0, limit);

    const concepts = await prisma.userMemoryConcept.findMany({
      where: { userId: ctx.userId },
      take: 100,
    });
    const matchedConcepts = concepts
      .filter(
        (c) =>
          c.name.toLowerCase().includes(kw) ||
          c.description.toLowerCase().includes(kw),
      )
      .slice(0, limit);

    const lines: string[] = [];
    for (const c of matchedConcepts) {
      lines.push(
        `[概念] ${c.name}（掌握度 ${c.masteryLevel.toFixed(2)}）: ${c.description.slice(0, 120)}`,
      );
    }
    for (const f of matched) {
      lines.push(`[${f.category}] ${f.content.slice(0, 120)}`);
    }

    return lines.length > 0 ? lines.join("\n") : "（无匹配记忆）";
  },
};
