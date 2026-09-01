#!/usr/bin/env python3
"""
CrawlEyes MCP Server
====================
把 CrawlEyes的搜索 + 抓取 + 深度调研能力暴露成标准 MCP 工具，任何 MCP 客户端
（Hermes / Claude / Cursor / 其他 agent）都能接入。

工具:
  - search(query, limit)     : 搜索。SearXNG 优先，失败自动 fallback Tavily keyless
  - extract(url, max_words, format) : 抓取网页正文为 Markdown（Crawl4AI + 正文去噪，多格式）
  - sitemap(origin)          : 站点地图发现（整站 URL 列表）
  - deep_research(topic)     : 深度调研（子问题分解 + 多源检索 + 汇总报告）

传输方式:
  stdio（默认）: 标准 MCP 客户端进程内接入，mcp_crawl_server.py 直接运行
  http（可选） : streamable-http 远程接入，任何能访问端口的客户端都能连:
      python -m crawleyes.mcp_crawl_server --transport http --port 8765 --host 127.0.0.1
      客户端连接 http://127.0.0.1:8765/mcp

用法:
  /path/to/your/crawl/.venv/bin/python /path/to/your/crawl/scripts/mcp_crawl_server.py
  # 或配置到 MCP 客户端:
  #   mcp_servers:
  #     crawl:
  #       command: "/path/to/your/crawl/.venv/bin/python"
  #       args: ["/path/to/your/crawl/scripts/mcp_crawl_server.py"]
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
from .rate_limit import default_limiter, retry_with_backoff
from .sanitize import sanitize_markdown

# 默认开启语义重排 (P2: fastembed + bge-small-zh)
_search = CrawlSearch(rerank=True)


def _search_once(query: str, limit: int) -> dict:
    """单次搜索（供统一重试包装）。"""
    return _search.search(query, limit=limit)


@mcp.tool()
def search(query: str, limit: int = 5) -> str:
    """搜索网页。SearXNG 优先，失败自动 fallback Tavily keyless（无需 API key）。
    带统一限流 + 指数退避重试（最多 3 次）。
    limit 会被 clamp 到 [1, 20]，防止传超大值打爆下游。
    Returns JSON with title/url/description for each result."""
    limit = max(1, min(limit, 20))  # P2-F: clamp 防止打爆下游
    default_limiter.acquire("search")
    try:
        result = retry_with_backoff(_search_once, query, limit, attempts=3)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"search failed after retries: {exc}"}, ensure_ascii=False)
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
    import asyncio

    from .crawl4ai_cli import scrape
    # P2-I: max_words 护栏 —— None/负值 归零（0=不截断全量）
    if max_words is None or max_words < 0:
        max_words = 0
    # 统一限流：extract 是重操作（启动浏览器），cost=3 防止并发打爆目标站
    # 用 to_thread 避免阻塞 MCP 事件循环
    await asyncio.to_thread(default_limiter.acquire, "extract", 3)
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
async def sitemap(origin: str, max_urls: int = 500) -> str:
    """发现站点 URL 地图。解析 {origin}/sitemap.xml（支持 index/gzip），
    缺失时回退 robots.txt 的 Sitemap 声明。返回去重后的 URL 列表 JSON。
    用于整站抓取 / deep_research 扩 URL 源。带统一限流。"""
    import asyncio

    from .sitemap import discover_sitemap_urls
    # P2-G: sitemap 接统一限流（urllib 同步阻塞 → to_thread 避免阻塞事件循环）
    await asyncio.to_thread(default_limiter.acquire, "sitemap", 2)
    urls = await asyncio.to_thread(discover_sitemap_urls, origin, max_urls=max_urls)
    return json.dumps({"origin": origin, "count": len(urls), "urls": urls}, ensure_ascii=False)


@mcp.tool()
async def deep_research(topic: str, num_questions: int = 4, per_q: int = 3) -> str:
    """深度调研：把主题拆成子问题→搜索→抓取→合成带引用的报告。
    Uses SearXNG → Tavily keyless (no API key). Optional LLM synthesis via
    CRAWLEYES_LLM_* env vars; without them returns an evidence-aggregate report.
    Returns JSON with report (Markdown) + sources."""
    import asyncio

    from .deep_research import deep_research as run_research
    # deep_research 是最重的操作（多轮搜索+抓取+LLM），cost=5 强限流防并发
    await asyncio.to_thread(default_limiter.acquire, "deep_research", 5)
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
    """Console entry point for the MCP server.

    Default transport is stdio (standard MCP clients). Pass --transport http
    to serve over HTTP (streamable-http) for remote clients, e.g.:
        python scripts/mcp_crawl_server.py --transport http --port 8765
    """
    import argparse
    parser = argparse.ArgumentParser(description="CrawlEyes MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="传输方式: stdio(默认, MCP 客户端标准) / http(streamable-http 远程)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 端口 (transport=http 时)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址 (transport=http 时)")
    args = parser.parse_args()

    if args.transport == "http":
        # streamable-http 的 host/port 由 FastMCP settings 控制，需先设置
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # 提示远程客户端连接方式
        print(f"CrawlEyes MCP over HTTP: http://{args.host}:{args.port}/mcp", flush=True)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
