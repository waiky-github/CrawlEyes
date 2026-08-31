"""CrawlEyes - Web Scraping & Search Toolkit for AI Agents.

Provides:
- CrawlSearch: SearXNG-first search with Tavily keyless fallback + optional semantic rerank
- MCP server: exposes search() + extract() as standard MCP tools (crawleyes-mcp)
- RAG-ready interfaces: markdown(url) / search_markdown(query) one-liners
- Deep research: deep_research(topic) → cited Markdown report (SearXNG + LLM optional)
"""

__version__ = "0.1.0"

from .crawl_search_standalone import CrawlSearch
from .deep_research import deep_research, deep_research_sync
from .rag import markdown, markdown_sync, search_markdown

__all__ = [
    "CrawlSearch",
    "deep_research",
    "deep_research_sync",
    "markdown",
    "markdown_sync",
    "search_markdown",
    "__version__",
]
