"""CrawlEyes - Web Scraping & Search Toolkit for AI Agents.

Provides:
- CrawlSearch: SearXNG-first search with Tavily keyless fallback + optional semantic rerank
- MCP server: exposes search() + extract() as standard MCP tools (crawleyes-mcp)
"""

__version__ = "0.1.0"

from .crawl_search_standalone import CrawlSearch

__all__ = ["CrawlSearch", "__version__"]
