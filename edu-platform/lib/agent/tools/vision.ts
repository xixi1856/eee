/**
 * Vision tool: analyzeImage
 * Lets the main chat model (e.g. deepseek-v4-pro) delegate image understanding
 * to the dedicated vision model (e.g. qwen3.6-plus) by passing image URLs and a question.
 */

import OpenAI from "openai";
import { getLLMClient, getVisionModel } from "../llm-registry";
import type { Tool, TurnContext } from "../types";

export const analyzeImageTool: Tool = {
  name: "analyzeImage",
  description:
    "使用视觉模型（如 qwen3.6-plus）对图片内容进行详细分析或针对性提问。" +
    "当需要深入理解图片细节、提取文字、分析图表、识别公式时使用。" +
    "image_urls 中填写消息里提供的图片 URL。",
  category: "read",
  parameters: {
    type: "object",
    properties: {
      image_urls: {
        type: "array",
        items: { type: "string" },
        description: "要分析的图片 URL 列表（presigned URL 或 data URI）",
      },
      question: {
        type: "string",
        description: "对图片提出的具体问题，例如：「图中有哪些网络节点？」「图中的公式是什么？」",
      },
    },
    required: ["image_urls", "question"],
  },
  async execute(args: Record<string, unknown>, _ctx: TurnContext): Promise<string> {
    const imageUrls = Array.isArray(args.image_urls)
      ? (args.image_urls as unknown[]).filter((u): u is string => typeof u === "string")
      : [];
    const question = typeof args.question === "string" ? args.question.trim() : "";

    if (imageUrls.length === 0) {
      return JSON.stringify({ error: "缺少 image_urls 参数" });
    }
    if (!question) {
      return JSON.stringify({ error: "缺少 question 参数" });
    }

    try {
      const client = getLLMClient("vision");
      const model = getVisionModel();

      const contentParts: OpenAI.Chat.ChatCompletionContentPart[] = [
        { type: "text", text: question },
        ...imageUrls.map((url) => ({
          type: "image_url" as const,
          image_url: { url },
        })),
      ];

      const resp = await client.chat.completions.create({
        model,
        messages: [{ role: "user", content: contentParts }],
        max_tokens: 1500,
      });

      const answer = resp.choices[0]?.message?.content?.trim() ?? "";
      return answer || JSON.stringify({ error: "视觉模型未返回内容" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return JSON.stringify({ error: `视觉模型调用失败：${msg}` });
    }
  },
};
