"""Sitemap discovery for CrawlEyes.

Parses ``sitemap.xml`` (and its gzip / index variants) into a flat, deduped
list of URLs. Lets callers discover the URL surface of a site without crawling
it — useful as an extra source for ``deep_research`` or for whole-site fetch.

Supported:
- Plain XML sitemap (``<urlset><url><loc>...``)
- Sitemap index (``<sitemapindex><sitemap><loc>...``) — recursed, depth-bounded
- ``.gz`` gzip-compressed sitemaps (detected by header bytes / extension)
- ``robots.txt``-declared sitemap location (``Sitemap:`` lines) — optional
  fallback when ``{origin}/sitemap.xml`` is missing

Pure stdlib (urllib + xml.etree), fail-open (any parse error returns the URLs
we already have).
"""

from __future__ import annotations

import gzip
import logging
import urllib.request
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

_USER_AGENT = "CrawlEyes/0.1 (+https://github.com/waiky-github/CrawlEyes)"
_TIMEOUT = 15
_MAX_DEPTH = 3
_MAX_URLS = 2000
_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = resp.read()
    if data[:2] == b"\x1f\x8b":  # gzip magic
        data = gzip.decompress(data)
    return data


def _urls_from_xml(data: bytes) -> tuple[list[str], list[str]]:
    """Parse XML, return (leaf urls, nested sitemap urls)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        logger.debug("sitemap XML parse failed: %s", exc)
        return [], []

    leaf, nested = [], []
    # urlset (leaf urls)
    for loc in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        text = (loc.text or "").strip()
        if not text:
            continue
        if root.tag.endswith("sitemapindex"):
            nested.append(text)
        else:
            leaf.append(text)
    return leaf, nested


def parse_sitemap(sitemap_url: str, *, max_urls: int = _MAX_URLS,
                  max_depth: int = _MAX_DEPTH) -> list[str]:
    """Fetch and parse a sitemap (or sitemap index) into a deduped URL list.

    Recurses into nested sitemap indexes up to ``max_depth`` levels, caps the
    result at ``max_urls``, and never raises — failures return what we have.
    """
    seen: set[str] = set()
    result: list[str] = []

    def walk(url: str, depth: int) -> None:
        if depth > max_depth or len(result) >= max_urls or url in seen:
            return
        seen.add(url)
        try:
            data = _fetch(url)
        except Exception as exc:
            logger.debug("sitemap fetch failed %s: %s", url, exc)
            return
        leaf, nested = _urls_from_xml(data)
        for u in leaf:
            if u and u not in result:
                result.append(u)
            if len(result) >= max_urls:
                return
        for child in nested:
            walk(child, depth + 1)

    walk(sitemap_url, 0)
    return result


def discover_sitemap_urls(origin: str, *, max_urls: int = _MAX_URLS,
                          max_depth: int = _MAX_DEPTH) -> list[str]:
    """Best-effort URL discovery for an origin.

    Tries ``{origin}/sitemap.xml`` first; if that yields nothing, falls back to
    a ``Sitemap:`` location declared in ``{origin}/robots.txt``. Returns a
    deduped URL list (possibly empty — never raises).
    """
    origin = origin.rstrip("/")
    urls = parse_sitemap(f"{origin}/sitemap.xml", max_urls=max_urls, max_depth=max_depth)
    if urls:
        return urls

    # fallback: robots.txt Sitemap: directive
    try:
        data = _fetch(f"{origin}/robots.txt")
        text = data.decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                loc = line.split(":", 1)[1].strip()
                if loc:
                    return parse_sitemap(loc, max_urls=max_urls, max_depth=max_depth)
    except Exception as exc:
        logger.debug("robots.txt sitemap discovery failed: %s", exc)
    return []

