"""RAG-ready interfaces for CrawlEyes.

One-liner entry points that turn any URL (or search query) into clean, sanitized,
LLM-ready Markdown — the building blocks for RAG corpora and deep-research agents.

- ``markdown(url)``: fetch + denoise + strip prompt-injection → clean Markdown
- ``search_markdown(query)``: search → return top results as Markdown (title + url + snippet)
"""
from __future__ import annotations

import asyncio
from typing import Any

from .crawl_search_standalone import CrawlSearch
from .sanitize import sanitize_markdown

# Default instance (semantic rerank on, like the MCP server)
_search = CrawlSearch(rerank=True)


async def markdown(
    url: str,
    max_words: int = 8000,
    noise_filter: bool = True,
    retry: int = 2,
    timeout: int = 30,
    text_only: bool = False,
) -> dict[str, Any]:
    """Fetch a URL and return clean, sanitized Markdown (RAG-ready).

    Applies Crawl4AI extraction + content denoising + prompt-injection stripping.
    Returns ``{"success": True, "title", "url", "length", "markdown"}`` on success,
    or ``{"success": False, "error", "url"}`` on failure. Never raises.
    """
    from .crawl4ai_cli import scrape

    result = await scrape(
        url=url,
        timeout=timeout,
        max_words=max_words,
        text_only=text_only,
        noise_filter=noise_filter,
        retry=retry,
    )
    if not result.get("success"):
        return result

    clean = sanitize_markdown(result.get("markdown", ""), max_words=max_words)
    result["markdown"] = clean
    result["length"] = len(clean.split())
    return result


def search_markdown(query: str, limit: int = 5) -> dict[str, Any]:
    """Search (SearXNG → Tavily keyless) and return top results as Markdown.

    Returns ``{"success": True, "source", "markdown"}`` where markdown is a compact
    list of ``- [title](url): snippet`` lines, or ``{"success": False, "error"}``.
    """
    result = _search.search(query, limit=limit)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "unknown")}

    web = result.get("data", {}).get("web", [])
    source = result.get("source") or result.get("data", {}).get("source_tag", "?")
    lines = [f"# Search results for: {query}", f"_source: {source}_", ""]
    for item in web:
        title = item.get("title", "")
        url = item.get("url", "")
        desc = (item.get("description") or "").strip()
        lines.append(f"- [{title}]({url})" + (f": {desc}" if desc else ""))
    return {
        "success": True,
        "source": source,
        "count": len(web),
        "markdown": "\n".join(lines),
    }


def markdown_sync(url: str, **kwargs) -> dict[str, Any]:
    """Synchronous wrapper around :func:`markdown` for non-async callers."""
    return asyncio.run(markdown(url, **kwargs))


__all__ = ["markdown", "markdown_sync", "sanitize_markdown", "search_markdown"]
