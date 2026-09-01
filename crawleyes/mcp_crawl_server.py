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
  python scripts/mcp_crawl_server.py
  # 或配置到 MCP 客户端:
  #   mcp_servers:
  #     crawl:
  #       command: "/path/to/your/venv/bin/python"
  #       args: ["/path/to/crawl/scripts/mcp_crawl_server.py"]
  #       env: { SEARXNG_URL: "https://your-searxng" }
"""
import json
import os
import sys

# 保证能 import 插件和脚本
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crawl-search")

# 复用独立搜索实现（不依赖 Hermes agent 包）
from .crawl_search_standalone import CrawlSearch
from .sanitize import sanitize_markdown

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
async def extract(url: str, max_words: int = 8000, respect_robots: bool = False,
                  format: str = "markdown") -> str:
    """抓取网页正文为 Markdown。自动去噪（过滤导航/广告），失败自动重试。
    返回前会剥离隐藏文字 + 标记可疑注入，防止 prompt-injection 劫持 Agent。
    respect_robots=True 时先检查目标站 robots.txt (RFC 9309)，被 Disallow 则拒绝。
    format: markdown(默认) | fit | raw | markdown_with_citations。
    Returns JSON with title/markdown/length."""
    from .crawl4ai_cli import scrape
    result = await scrape(
        url=url, max_words=max_words, retry=2, timeout=30, noise_filter=True,
        respect_robots=respect_robots, output_format=format,
    )
    if not result.get("success"):
        return json.dumps({"error": result.get("error", "unknown")}, ensure_ascii=False)
    markdown = sanitize_markdown(result.get("markdown", ""), max_words=max_words)
    return json.dumps({
        "title": result.get("title"),
        "url": result.get("url"),
        "length": len(markdown.split()),
        "markdown": markdown,
    }, ensure_ascii=False)


@mcp.tool()
async def deep_research(topic: str, num_questions: int = 4, per_q: int = 3) -> str:
    """深度调研：把主题拆成子问题→搜索→抓取→合成带引用的报告。
    Uses SearXNG → Tavily keyless (no API key). Optional LLM synthesis via
    CRAWLEYES_LLM_* env vars; without them returns an evidence-aggregate report.
    Returns JSON with report (Markdown) + sources."""
    from .deep_research import deep_research as run_research
    result = await run_research(topic, num_questions=num_questions, per_q=per_q)
    return json.dumps({
        "topic": result.topic,
        "mode": result.mode,
        "sub_questions": result.sub_questions,
        "source_count": len(result.sources),
        "sources": [{"title": s["title"], "url": s["url"]} for s in result.sources],
        "report": result.report,
        "errors": result.errors,
    }, ensure_ascii=False)


def main():
    """Console entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
