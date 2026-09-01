"""Robots.txt (RFC 9309) compliance helper for CrawlEyes.

Lets callers optionally check whether a URL is allowed by the target site's
robots.txt before scraping. Uses Python's stdlib ``urllib.robotparser``
(the same engine Scrapy uses) — zero new dependencies.

Design decisions:
- **Default off**: respecting robots is *opt-in* via ``respect_robots=True``.
  CrawlEyes is a general-purpose fetch toolkit; many sites ship aggressive
  robots.txt (or none), and forcing compliance would silently block a lot of
  legitimately-scrapable content. Compliance is the caller's informed choice.
- **Fail-open**: if robots.txt can't be fetched (timeout / 404 / parse error)
  the request is *allowed* — we never block on a robots we couldn't read.
- **Cached per host**: robots.txt is fetched once per host and cached, so
  repeated scrapes of the same site don't re-fetch it every time.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("crawleyes.robots")

# host -> (expires_at, parser)  — robots.txt is cheap to re-read; 15 min TTL
_CACHE: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = {}
_TTL = 900  # seconds
_FETCH_TIMEOUT = 5.0

_UA = "CrawlEyes/0.1 (+https://github.com/waiky-github/CrawlEyes)"


def _cache_get(host: str) -> urllib.robotparser.RobotFileParser | None:
    entry = _CACHE.get(host)
    if entry and entry[0] > time.time():
        return entry[1]
    return None


def _cache_put(host: str, parser: urllib.robotparser.RobotFileParser) -> None:
    _CACHE[host] = (time.time() + _TTL, parser)


async def check_robots(url: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for ``url`` per the site's robots.txt.

    Fail-open: any fetch/parse problem → ``(True, ...)`` (allowed).
    Explicit ``Disallow`` → ``(False, ...)`` (blocked).
    """
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return True, "invalid url"
        host = parts.netloc
        robots_url = f"{parts.scheme}://{host}/robots.txt"

        parser = _cache_get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            try:
                async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
                    resp = await client.get(robots_url, headers={"User-Agent": _UA})
                if resp.status_code == 200:
                    # robotparser.read() wants the file *contents*; we feed the
                    # text directly so network handling stays with httpx.
                    parser.parse(resp.text.splitlines())
                else:
                    # 404 (no robots) or 4xx/5xx → allow, don't cache a broken one
                    return True, f"robots.txt HTTP {resp.status_code}"
            except Exception as exc:
                log.debug("robots fetch failed for %s: %s", host, exc)
                return True, f"robots fetch error: {exc}"
            _cache_put(host, parser)

        allowed = parser.can_fetch("*", url)
        if allowed:
            return True, "allowed by robots.txt"
        return False, "disallowed by robots.txt"
    except Exception as exc:
        log.debug("robots check error for %s: %s", url, exc)
        return True, f"robots check error: {exc}"


# Re-export for convenience
__all__ = ["check_robots"]
