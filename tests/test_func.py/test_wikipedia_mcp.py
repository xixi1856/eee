import os
import sys
import asyncio
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROXY_URL = "http://127.0.0.1:7890"
query = " ".join(sys.argv[1:])

MCP_ENV = {
    **os.environ,
    "HTTP_PROXY": PROXY_URL,
    "HTTPS_PROXY": PROXY_URL,
    "http_proxy": PROXY_URL,
    "https_proxy": PROXY_URL,
}

SERVER_COMMAND = str(Path(sys.executable).with_name("wikipedia-mcp.exe"))

# 配置 Server 参数
server_params = StdioServerParameters(
    command=SERVER_COMMAND,
    args=["--transport", "stdio", "--language", "en"],
    env=MCP_ENV,
)


async def main():
    if not query:
        print("用法: python tests/test_func.py/test_wikipedia.py <关键词>")
        return

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化连接
            await session.initialize()

            # 列出可用工具
            tools = await session.list_tools()
            print(f"可用工具: {tools}")

            available = {t.name for t in tools.tools}
            candidates = ["search", "wikipedia_search", "search_wikipedia"]
            tool_name = next((name for name in candidates if name in available), None)
            if tool_name is None:
                print(f"未找到搜索工具，可用工具名: {sorted(available)}")
                return

            # 调用维基百科搜索工具
            result = await session.call_tool(tool_name, {"query": query})
            print(f"搜索结果：{result}")


if __name__ == "__main__":
    asyncio.run(main())