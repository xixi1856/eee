"""Web and Wikipedia search tools.

Toolset: search
Tools: web_search, web_fetch, ollama_web_search, wikipedia_search
"""

from __future__ import annotations

import ipaddress
import logging
import urllib.parse
from typing import Any

from edu_agent.tool_payloads import tool_error, tool_result
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.runtime_context import get_current_runtime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SCHEMA_WEB_SEARCH = {
    "name": "web_search",
    "description": (
        "通过 Tavily API（优先）或 DuckDuckGo 搜索互联网，返回相关网页的标题、URL 和摘要。"
        "适用于查询实时资讯、政策热点、最新事件等知识库未涵盖的内容。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
            "max_results": {"type": "integer", "description": "返回结果数量（默认 5，最多 10）"},
        },
        "required": ["query"],
    },
}

_SCHEMA_WEB_FETCH = {
    "name": "web_fetch",
    "description": (
        "抓取指定 URL 的网页正文内容（静态 HTML），提取纯文本。"
        "配合 web_search 使用：先搜索得到 URL，再用此工具获取详细内容。"
        "不支持需要 JavaScript 渲染的页面。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页 URL"},
            "max_chars": {"type": "integer", "description": "返回正文最大字符数（默认 8000）"},
        },
        "required": ["url"],
    },
}

_SCHEMA_OLLAMA_WEB_SEARCH = {
    "name": "ollama_web_search",
    "description": (
        "使用 Ollama 官方 Web Search API（https://ollama.com/api/web_search）搜索互联网。"
        "需要设置环境变量 OLLAMA_API_KEY（在 https://ollama.com/settings/keys 创建）。"
        "与 web_search 互补：web_search 使用 Tavily/DuckDuckGo，此工具使用 Ollama 自有搜索服务。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言问题"},
            "max_results": {"type": "integer", "description": "返回结果数量（默认 5，最多 10）"},
        },
        "required": ["query"],
    },
}

_SCHEMA_WIKIPEDIA_SEARCH = {
    "name": "wikipedia_search",
    "description": (
        "从维基百科检索某个概念或术语的解释，用于补充知识库中未涵盖的通用知识点。"
        "当知识库查询结果不足或需要百科级背景知识时调用。"
        "若返回结果为歧义页，请根据候选词列表用更具体的词重新调用本工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查询的概念名称或关键词，建议使用精确术语",
            },
            "lang": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "查询语言：zh（中文，默认）或 en（英文）",
            },
            "summary_only": {
                "type": "boolean",
                "description": (
                    "是否只返回摘要（默认 true，节省 token）。"
                    "设为 false 时额外返回前 3 个章节内容。"
                ),
            },
            "max_chars": {
                "type": "integer",
                "description": "返回内容的最大字符数，默认 500。summary_only=false 时各 section 按比例截断。",
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Process-scoped Wikipedia cache (no TTL needed)
_WIKI_CACHE: dict[str, str] = {}

# Phrases indicating a disambiguation page
_DISAMBIG_MARKERS = ("may refer to", "可以指", "可以是", "可能指")

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _is_ssrf_url(url: str) -> bool:
    """Return True if *url* targets a private/loopback/link-local address."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if host.lower() in ("localhost", "localhost.localdomain"):
            return True
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        host_lower = (urllib.parse.urlparse(url).hostname or "").lower()
        return host_lower in ("localhost",) or host_lower.endswith(".local")


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Scrape DuckDuckGo HTML results (no API key required)."""
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "kl": "cn-zh"}
    try:
        resp = httpx.post(url, data=params, headers=_DDG_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("DDG search failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select(".result__body")[:max_results]:
        title_el = item.select_one(".result__title")
        url_el = item.select_one(".result__url")
        snippet_el = item.select_one(".result__snippet")
        title = title_el.get_text(strip=True) if title_el else ""
        link = url_el.get_text(strip=True) if url_el else ""
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title or link:
            if link and not link.startswith("http"):
                link = "https://" + link
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _handle_web_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("缺少必要参数：query")
    max_results = max(1, min(int(args.get("max_results", 5)), 10))
    results: list[dict] = []

    # Try Tavily first
    try:
        ctx = get_current_runtime()
        tavily_key = (ctx.settings.tools.tavily_api_key or "").strip()
        if tavily_key:
            import httpx
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": max_results},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                })
    except Exception as exc:
        logger.info("Tavily search failed, falling back to DDG: %s", exc)

    if not results:
        results = _ddg_search(query, max_results)

    if not results:
        return tool_error("搜索未返回任何结果")

    lines = [f"**搜索结果：{query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r['title']}**")
        lines.append(f"   🔗 {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return tool_result("\n".join(lines), payload=results)


async def _handle_web_fetch(args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return tool_error("缺少必要参数：url")
    if _is_ssrf_url(url):
        return tool_error("拒绝访问内部地址（SSRF 防护）")
    max_chars = int(args.get("max_chars", 8000))
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return tool_error("缺少依赖：httpx 和 beautifulsoup4")

    try:
        resp = httpx.get(url, headers=_DDG_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return tool_error(f"请求失败：{exc}")

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    body = "\n\n".join(paragraphs)

    text = f"# {title}\n\n{body}" if title else body
    return tool_result(text[:max_chars])


async def _handle_ollama_web_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("缺少必要参数：query")
    max_results = max(1, min(int(args.get("max_results", 5)), 10))
    ctx = get_current_runtime()
    api_key = (ctx.settings.tools.ollama_api_key or "").strip()
    if not api_key:
        return tool_error(
            "未设置 OLLAMA_API_KEY 环境变量，"
            "请在 https://ollama.com/settings/keys 创建 API Key 后设置"
        )

    try:
        import httpx
        resp = httpx.post(
            "https://ollama.com/api/web_search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": max_results},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
        return tool_error(
            f"Ollama Web Search API 返回错误 {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )
    except Exception as exc:
        return tool_error(f"请求失败: {exc}")

    results = data.get("results", [])
    if not results:
        return tool_result("搜索未返回结果", payload=[])

    lines = [f"**Ollama Web Search：{query}**\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r.get('title', '(无标题)')}**")
        lines.append(f"   🔗 {r.get('url', '')}")
        content = r.get("content", "").strip()
        if content:
            lines.append(f"   {content[:300]}{'…' if len(content) > 300 else ''}")
        lines.append("")
    return tool_result("\n".join(lines), payload=results)


async def _handle_wikipedia_search(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("缺少必要参数：query")
    lang: str = args.get("lang", "zh")
    summary_only: bool = bool(args.get("summary_only", True))
    max_chars: int = int(args.get("max_chars", 500))

    import wikipediaapi

    ctx = get_current_runtime()

    def _build_wiki(language: str) -> Any:
        proxy = (ctx.settings.tools.http_proxy or "").strip() or None
        kwargs: dict[str, Any] = {
            "user_agent": "EduAgent/1.0",
            "language": language,
            "extract_format": wikipediaapi.ExtractFormat.WIKI,
            "timeout": 20.0,
        }
        if proxy:
            kwargs["proxy"] = proxy
        return wikipediaapi.Wikipedia(**kwargs)

    def _is_disambiguation(page: Any) -> bool:
        summary_head = page.summary[:400]
        if any(marker in summary_head for marker in _DISAMBIG_MARKERS):
            return True
        for cat in page.categories:
            if "disambiguation" in cat.lower() or "消歧义" in cat:
                return True
        return False

    def _format_content(page: Any) -> str:
        if summary_only:
            return page.summary[:max_chars]
        section_budget = max_chars // 3
        parts = [page.summary[:max_chars]]
        for section in list(page.sections)[:3]:
            if section.text.strip():
                parts.append(f"### {section.title}\n{section.text[:section_budget]}")
        return "\n\n".join(parts)

    def _fetch(language: str) -> tuple[bool, str]:
        """Return (success, text_or_error)."""
        cache_key = f"{language}:{query}:{summary_only}:{max_chars}"
        if cache_key in _WIKI_CACHE:
            logger.debug("wikipedia_search cache hit: %s", cache_key)
            return True, _WIKI_CACHE[cache_key]

        wiki = _build_wiki(language)
        try:
            page = wiki.page(query)
        except Exception as exc:
            logger.warning("wikipedia_search network error (%s): %s", language, exc)
            return False, f"网络请求失败：{exc}"

        if not page.exists():
            return False, f"未在 {language} 维基百科中找到词条「{query}」"

        if _is_disambiguation(page):
            candidates = list(page.links.keys())[:10]
            candidate_str = "\n".join(f"- {c}" for c in candidates)
            msg = (
                f"[歧义页] 「{query}」在维基百科中为歧义词条，可能指代以下内容，"
                f"请使用更具体的词重新调用 wikipedia_search：\n{candidate_str}"
            )
            logger.info("wikipedia_search disambiguation: %s", query)
            return True, msg

        content = _format_content(page)
        _WIKI_CACHE[cache_key] = content
        logger.info("wikipedia_search fetched %d chars for「%s」(%s)", len(content), query, language)
        return True, content

    success, text = _fetch(lang)
    if not success and lang == "zh":
        logger.info("wikipedia_search zh→en fallback for「%s」", query)
        success, text = _fetch("en")
        if success and not text.startswith("[歧义页]"):
            text = f"[来源：英文维基百科]\n{text}"

    if not success:
        return tool_error(text)
    return tool_result(text)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_WEB_SEARCH["name"],
        description=_SCHEMA_WEB_SEARCH["description"],
        input_schema=_SCHEMA_WEB_SEARCH["parameters"],
        handler=_handle_web_search,
        toolset="search",
        permissions=[ToolPermission.NETWORK],
        emoji="🌐",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_WEB_FETCH["name"],
        description=_SCHEMA_WEB_FETCH["description"],
        input_schema=_SCHEMA_WEB_FETCH["parameters"],
        handler=_handle_web_fetch,
        toolset="search",
        permissions=[ToolPermission.NETWORK],
        emoji="🌍",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_OLLAMA_WEB_SEARCH["name"],
        description=_SCHEMA_OLLAMA_WEB_SEARCH["description"],
        input_schema=_SCHEMA_OLLAMA_WEB_SEARCH["parameters"],
        handler=_handle_ollama_web_search,
        toolset="search",
        permissions=[ToolPermission.NETWORK],
        emoji="🔭",
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_WIKIPEDIA_SEARCH["name"],
        description=_SCHEMA_WIKIPEDIA_SEARCH["description"],
        input_schema=_SCHEMA_WIKIPEDIA_SEARCH["parameters"],
        handler=_handle_wikipedia_search,
        toolset="search",
        permissions=[ToolPermission.NETWORK],
        emoji="📖",
    )
)
