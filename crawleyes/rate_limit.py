"""Lightweight rate limiting + exponential backoff for the CrawlEyes MCP layer.

Why this exists: the underlying layers already do per-call retries
(Crawl4AI ``retry=``, SearXNG -> Tavily fallback, deep_research's internal
loops). What was missing is a **single choke point** at the MCP boundary so
that many concurrent agent calls can't hammer SearXNG / Tavily / the target
sites, and so transient failures get a consistent retry policy instead of
surfacing immediately to the caller.

Design notes
------------
- Thread-safe: MCP serves concurrent tool calls, so everything here uses a
  ``threading.Lock`` around shared counters/timestamps.
- Fail-open: if the limiter itself errors, calls pass through (never break
  the MCP tool because of our own guard).
- No extra deps: pure stdlib.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RateLimiter:
    """Per-key sliding-window rate limiter (thread-safe, fail-open).

    Usage::

        rl = RateLimiter()
        rl.acquire("search")          # blocks briefly if over limit
        rl.acquire("extract", cost=3) # heavier calls can cost more

    A window is ``window`` seconds wide; at most ``max_calls`` acquisitions
    are allowed per key inside that window. The limiter **sleeps** the caller
    for the remaining window time instead of raising, so an over-limit call
    simply waits its turn (good fit for a server that must not fail).
    """

    def __init__(self, max_calls: int = 10, window: float = 5.0,
                 default_cost: int = 1) -> None:
        self._max_calls = max_calls
        self._window = window
        self._default_cost = default_cost
        self._lock = threading.Lock()
        self._stamps: dict[str, list[tuple[float, int]]] = defaultdict(list)

    def acquire(self, key: str, cost: int | None = None, *, block: bool = True) -> bool:
        """Try to acquire ``cost`` units for ``key``.

        Returns True if acquired (or the limiter is bypassed). If ``block``
        and over the limit, sleeps until the window rolls over, then returns
        True. If not ``block`` and over the limit, returns False.
        """
        cost = cost or self._default_cost
        now = time.monotonic()
        try:
            with self._lock:
                window_start = now - self._window
                stamps = self._stamps[key]
                # drop stamps outside the window
                self._stamps[key] = [s for s in stamps if s[0] >= window_start]
                used = sum(s[1] for s in self._stamps[key])
                if used + cost <= self._max_calls:
                    self._stamps[key].append((now, cost))
                    return True
                # over the limit: compute how long until the oldest stamp leaves
                if not self._stamps[key]:
                    return True
                oldest = min(s[0] for s in self._stamps[key])
                wait = (oldest + self._window) - now
        except Exception:  # fail open
            logger.debug("rate limiter error, passing through: %s", exc_info=True)
            return True

        if not block:
            return False
        if wait > 0:
            time.sleep(min(wait, self._window))
        return True

    def reset(self, key: str | None = None) -> None:
        """Clear the window for ``key`` (or all keys when ``key`` is None)."""
        with self._lock:
            if key is None:
                self._stamps.clear()
            else:
                self._stamps.pop(key, None)


def retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    attempts: int = 3,
    base_delay: float = 0.8,
    backoff: float = 2.0,
    jitter: float = 0.2,
    retryable: Callable[[Exception], bool] | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``fn`` with exponential backoff on exceptions.

    Retries up to ``attempts`` total tries. Sleeps ``base_delay * backoff**n``
    between tries (plus a small random jitter so concurrent retries don't
    thundering-herd). Only retries when ``retryable(exc)`` is truthy; when
    ``retryable`` is None, all exceptions are retried.

    Returns the first successful result. Raises the last exception if all
    attempts fail.
    """
    import random

    last_exc: Exception | None = None
    for n in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if retryable is not None and not retryable(exc):
                raise
            if n < attempts - 1:
                delay = base_delay * (backoff**n)
                delay += random.uniform(0, jitter)
                logger.debug("retry %d/%d after %.2fs (err: %s)",
                             n + 1, attempts, delay, exc)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# module-level default limiter for the MCP server
default_limiter = RateLimiter(max_calls=10, window=5.0)
