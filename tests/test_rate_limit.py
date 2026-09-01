"""Offline tests for rate limiting + backoff (P1-2).

Deterministic, no network: tests the sliding-window limiter and the
exponential-backoff wrapper directly.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawleyes.rate_limit import RateLimiter, retry_with_backoff


def test_limiter_allows_within_limit():
    rl = RateLimiter(max_calls=3, window=60)
    assert rl.acquire("k") is True
    assert rl.acquire("k") is True
    assert rl.acquire("k") is True


def test_limiter_blocks_over_limit_nonblocking():
    rl = RateLimiter(max_calls=2, window=60)
    assert rl.acquire("k") is True
    assert rl.acquire("k") is True
    # third call over the 2-call limit, non-blocking -> False
    assert rl.acquire("k", block=False) is False


def test_limiter_separates_keys():
    rl = RateLimiter(max_calls=1, window=60)
    assert rl.acquire("a") is True
    assert rl.acquire("b") is True  # different key, unaffected


def test_limiter_cost_accounting():
    rl = RateLimiter(max_calls=4, window=60)
    assert rl.acquire("k", cost=3) is True
    # remaining budget 1, a cost-2 call should be refused non-blocking
    assert rl.acquire("k", cost=2, block=False) is False
    assert rl.acquire("k", cost=1) is True


def test_limiter_window_rolls_over():
    rl = RateLimiter(max_calls=1, window=0.2)
    assert rl.acquire("k") is True
    assert rl.acquire("k", block=False) is False
    time.sleep(0.25)
    assert rl.acquire("k", block=False) is True


def test_limiter_fail_open_on_bad_key():
    rl = RateLimiter(max_calls=1, window=60)
    # unhashable key shouldn't crash the caller (type: ignore - intentional)
    assert rl.acquire(["list-key"], block=False) is True  # type: ignore[arg-type]


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    result = retry_with_backoff(flaky, attempts=3, base_delay=0.01, jitter=0)
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_all_attempts():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise TimeoutError("boom")

    try:
        retry_with_backoff(always_fail, attempts=2, base_delay=0.01, jitter=0)
        raise AssertionError("should have raised")
    except TimeoutError:
        pass
    assert calls["n"] == 2


def test_retry_respects_retryable_predicate():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("not transient")

    # ValueError not in retryable set -> raises immediately, no retry
    try:
        retry_with_backoff(
            fn, attempts=3, base_delay=0.01, jitter=0,
            retryable=lambda e: isinstance(e, ConnectionError),
        )
        raise AssertionError("should have raised")
    except ValueError:
        pass
    assert calls["n"] == 1
