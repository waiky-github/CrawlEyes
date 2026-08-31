#!/usr/bin/env python3
"""
SearXNG-Tavily MCP Server
==========================
把 crawl 项目的搜索 + 抓取能力暴露成标准 MCP 工具，任何 MCP 客户端
（Hermes / Claude / Cursor / 其他 agent）都能通过 stdio 接入。

工具:
  - search(query, limit)    : 搜索。SearXNG 优先，失败自动 fallback Tavily keyless
  - extract(url, max_words) : 抓取网页正文为 Markdown（Crawl4AI + 正文去噪）

用法:
  crawl/.venv/bin/python scripts/mcp_crawl_server.py
  # 或配置到 MCP 客户端:
  #   mcp_servers:
  #     crawl:
  #       command: "/home/agentuser/crawl/.venv/bin/python"
  #       args: ["/home/agentuser/crawl/scripts/mcp_crawl_server.py"]
  #       env: { SEARXNG_URL: "https://your-searxng" }
"""
import asyncio, os, sys, json

# 保证能 import 插件和脚本
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crawl-search")

# 复用独立搜索实现（不依赖 Hermes agent 包）
from .crawl_search_standalone import CrawlSearch

# 默认开启语义重排 (P2: fastembed + bge-small-zh)
_search = CrawlSearch(rerank=True)


@mcp.tool()
def search(query: str, limit: int = 5) -> str:
    """搜索网页。SearXNG 优先，失败自动 fallback Tavily keyless（无需 API key）。
    Returns JSON with title/url/description for each result."""
    result = _search.search(query, limit=limit)
    if not result.get("success"):
        return json.dumps({"error": result.get("error", "unknown")}, ensure_ascii=False)
    web = result.get("data", {}).get("web", [])
    source = result.get("source") or result.get("data", {}).get("source_tag", "?")
    return json.dumps({"source": source, "results": web}, ensure_ascii=False)


@mcp.tool()
async def extract(url: str, max_words: int = 8000) -> str:
    """抓取网页正文为 Markdown。自动去噪（过滤导航/广告），失败自动重试。
    Returns JSON with title/markdown/length."""
    from .crawl4ai_cli import scrape
    result = await scrape(
        url=url, max_words=max_words, retry=2, timeout=30, noise_filter=True,
    )
    if not result.get("success"):
        return json.dumps({"error": result.get("error", "unknown")}, ensure_ascii=False)
    return json.dumps({
        "title": result.get("title"),
        "url": result.get("url"),
        "length": result.get("length"),
        "markdown": result.get("markdown", ""),
    }, ensure_ascii=False)


def main():
    """Console entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
