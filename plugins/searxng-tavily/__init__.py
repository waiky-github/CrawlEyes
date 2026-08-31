"""SearXNG→Tavily keyless 聚合搜索 provider 插件入口."""

from .provider import SearXNGTavilyWebSearchProvider


def register(ctx) -> None:
    """Plugin entry point — called once at load time."""
    ctx.register_web_search_provider(SearXNGTavilyWebSearchProvider())
