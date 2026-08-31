"""Basic offline tests for CrawlEyes core logic.

These tests deliberately avoid external network calls (SearXNG / Tavily / HuggingFace)
so they run deterministically in CI. They cover:

- CrawlSearch.search() return-shape contract
- Graceful degradation when SEARXNG_URL is not set (falls through to Tavily, which
  then fails without network -> success=False, not an exception)
- SQLite cache get/set round-trip
- MCP server module imports cleanly and exposes the expected tools
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawleyes.crawl_search_standalone import CrawlSearch, _Cache


def test_search_contract_without_searxng():
    """Without SEARXNG_URL and without network, search returns a structured dict (never raises)."""
    os.environ.pop("SEARXNG_URL", None)
    s = CrawlSearch(rerank=False)
    result = s.search("offline test query", limit=3)
    # Must be a dict with the documented keys; must not raise even when both backends fail
    assert isinstance(result, dict)
    assert "success" in result
    assert isinstance(result.get("data", {}), dict)
    assert isinstance(result.get("data", {}).get("web", []), list)
    # On a fully offline box success may be False, but the shape must still hold.
    # If it did succeed (e.g. Tavily reachable), web must be a list of dicts.
    if result.get("success"):
        for item in result["data"]["web"]:
            assert isinstance(item, dict)
            assert "title" in item
            assert "url" in item


def test_cache_roundtrip(tmp_path="."):
    """SQLite cache stores and retrieves a query's results (uses a temp file path)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        c = _Cache(db_path=os.path.join(d, "test_cache.sqlite"))
        c.set("hello world", [{"title": "t", "url": "u", "description": "d"}])
        got = c.get("hello world")
        assert got is not None
        assert got[0]["title"] == "t"
        # Miss returns None
        assert c.get("never inserted") is None


def test_mcp_server_imports_and_tools():
    """The MCP server module imports and registers exactly search + extract tools."""
    from crawleyes import mcp_crawl_server as m

    tm = getattr(m.mcp, "_tool_manager", None)
    if tm is not None and hasattr(tm, "list_tools"):
        names = sorted(t.name for t in tm.list_tools())
        assert set(names) == {"search", "extract"}, names
    # At minimum the two functions exist as decorated tools
    assert hasattr(m, "search")
    assert hasattr(m, "extract")
    assert callable(m.search)
    assert callable(m.extract)


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
