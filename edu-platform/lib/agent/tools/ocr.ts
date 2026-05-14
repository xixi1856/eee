/**
 * OCR / document-parsing tool: parse_document
 * Sends base64-encoded file content to the RAG service for text extraction.
 */

import type { Tool } from "../types";

export const parseDocumentTool: Tool = {
  name: "parse_document",
  description:
    "解析并提取文件（PDF、图片等）中的文字内容，将其作为可读文本返回。" +
    "当学习者上传或提及某个文件、需要从附件中读取内容时调用此工具。",
  parameters: {
    type: "object",
    properties: {
      filename: {
        type: "string",
        description: "文件名（含扩展名，如 lecture.pdf）",
      },
      base64_content: {
        type: "string",
        description: "文件内容的 base64 编码字符串（标准 base64，不含 data URI 前缀）",
      },
    },
    required: ["filename", "base64_content"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const filename = typeof args.filename === "string" ? args.filename.trim() : "document.pdf";
    const base64Content =
      typeof args.base64_content === "string" ? args.base64_content.trim() : "";

    if (!base64Content) {
      return JSON.stringify({ error: "缺少必要参数：base64_content" });
    }

    const ragUrl = (process.env.RAG_SERVICE_URL ?? "http://localhost:8001").replace(/\/+$/, "");
    const ragKey = process.env.RAG_SERVICE_API_KEY ?? "";

    let res: Response;
    try {
      res = await fetch(`${ragUrl}/rag/parse-document`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(ragKey ? { "x-internal-key": ragKey } : {}),
        },
        body: JSON.stringify({ filename, base64_content: base64Content }),
      });
    } catch (err) {
      return JSON.stringify({ error: `RAG 服务不可用：${String(err)}` });
    }

    if (!res.ok) {
      const msg = await res.text().catch(() => "");
      return JSON.stringify({ error: `解析失败（${res.status}）：${msg.slice(0, 300)}` });
    }

    const data = (await res.json()) as { text?: string; pages?: number };
    const text = data.text ?? "";
    const pages = typeof data.pages === "number" ? data.pages : null;

    if (!text.trim()) {
      return JSON.stringify({ warning: "文件中未检测到可提取的文字内容", pages });
    }

    const truncated = text.length > 12000 ? text.slice(0, 12000) + "\n\n…（内容已截断）" : text;
    return pages !== null
      ? `【文档共 ${pages} 页，已提取文字内容】\n\n${truncated}`
      : truncated;
  },
};
