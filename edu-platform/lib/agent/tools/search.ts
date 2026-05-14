/**
 * Web search tools: web_search, wikipedia_search
 * Uses Tavily API (if configured) or DuckDuckGo as fallback.
 */

import type { Tool } from "../types";

// ---- SSRF guard (block private IP ranges) ----------------------------------

function _isPrivateUrl(rawUrl: string): boolean {
  try {
    const u = new URL(rawUrl);
    const hostname = u.hostname;
    if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1") return true;
    const parts = hostname.split(".").map(Number);
    if (parts.length === 4) {
      const [a, b] = parts;
      if (a === 10) return true;
      if (a === 172 && b >= 16 && b <= 31) return true;
      if (a === 192 && b === 168) return true;
    }
    return false;
  } catch {
    return true; // malformed URL → block
  }
}

// ---- Tavily helper ---------------------------------------------------------

async function _tavilySearch(
  query: string,
  maxResults: number,
): Promise<string> {
  const apiKey = process.env.TAVILY_API_KEY;
  if (!apiKey) throw new Error("TAVILY_API_KEY not set");

  const res = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, query, max_results: maxResults }),
  });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`Tavily ${res.status}: ${t.slice(0, 200)}`);
  }
  type TavilyResult = { title: string; url: string; content: string };
  const data = (await res.json()) as { results?: TavilyResult[] };
  const results = data.results ?? [];
  return results
    .map((r, i) => `[${i + 1}] ${r.title}\nURL: ${r.url}\n${r.content.slice(0, 400)}`)
    .join("\n\n");
}

// ---- Wikipedia helper ------------------------------------------------------

async function _wikiSearch(query: string): Promise<string> {
  const encoded = encodeURIComponent(query);
  const res = await fetch(
    `https://zh.wikipedia.org/api/rest_v1/page/summary/${encoded}`,
    { headers: { "User-Agent": "EduAgent/1.0" } },
  );
  if (res.status === 404) {
    // Try search endpoint
    const sr = await fetch(
      `https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encoded}&format=json&srlimit=5`,
    );
    if (!sr.ok) return "（维基百科搜索失败）";
    const sd = (await sr.json()) as { query?: { search?: Array<{ title: string; snippet: string }> } };
    const items = sd.query?.search ?? [];
    if (items.length === 0) return "（未找到维基百科相关词条）";
    const lines = items.map((s) => `- **${s.title}**: ${s.snippet.replace(/<[^>]+>/g, "")}`);
    return `相关词条：\n${lines.join("\n")}`;
  }
  if (!res.ok) return "（维基百科请求失败）";
  type WikiSummary = { title: string; extract: string; type: string };
  const d = (await res.json()) as WikiSummary;
  if (d.type === "disambiguation") {
    return `"${query}" 是歧义词条。请使用更具体的名称重新查询。\n摘要：${d.extract.slice(0, 400)}`;
  }
  return `**${d.title}**\n${d.extract.slice(0, 1500)}`;
}

// ---- Tools -----------------------------------------------------------------

export const webSearchTool: Tool = {
  name: "web_search",
  description:
    "通过 Tavily API 搜索互联网，返回相关网页的标题、URL 和摘要。" +
    "适用于查询实时资讯、政策热点、最新事件等知识库未涵盖的内容。",
  parameters: {
    type: "object",
    properties: {
      query: { type: "string", description: "搜索关键词或自然语言问题" },
      max_results: { type: "integer", description: "返回结果数量（默认 5，最多 10）" },
    },
    required: ["query"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const query = typeof args.query === "string" ? args.query.trim() : "";
    if (!query) return JSON.stringify({ error: "query 不能为空" });
    const maxResults =
      typeof args.max_results === "number" ? Math.max(1, Math.min(10, args.max_results)) : 5;
    try {
      return await _tavilySearch(query, maxResults);
    } catch (err) {
      return JSON.stringify({ error: `搜索失败: ${err instanceof Error ? err.message : String(err)}` });
    }
  },
};

export const wikipediaSearchTool: Tool = {
  name: "wikipedia_search",
  description:
    "从维基百科检索某个概念或术语的解释，用于补充知识库中未涵盖的通用知识点。" +
    "当知识库查询结果不足或需要百科级背景知识时调用。",
  parameters: {
    type: "object",
    properties: {
      query: { type: "string", description: "概念或术语名称" },
    },
    required: ["query"],
  },
  async execute(args: Record<string, unknown>): Promise<string> {
    const query = typeof args.query === "string" ? args.query.trim() : "";
    if (!query) return JSON.stringify({ error: "query 不能为空" });
    try {
      return await _wikiSearch(query);
    } catch (err) {
      return JSON.stringify({ error: String(err) });
    }
  },
};
