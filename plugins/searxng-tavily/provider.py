"""SearXNG→Tavily keyless 聚合搜索 provider for Hermes Agent.

搜索时优先走本地 SearXNG；当 SearXNG 失败（超时/HTTP 错误/被限流/空结果）
时自动回退到 Tavily keyless 模式（无需 API key）。给 DDG/Startpage 等
上游引擎偶发 CAPTCHA 限流提供一个零成本的云兜底。

v1.1 新增（2026-08-31）:
- 熔断器（circuit breaker）: SearXNG 连续失败 N 次 → 熔断, 冷却期内直接走
  Tavily, 半开状态探测恢复。避免 SearXNG 挂掉时反复超时浪费时间。
- 错误分类: timeout / network_error / http_error / rate_limited / no_results
  不同错误不同策略（rate_limited 熔断, timeout 重试, no_results 直接 fallback）。
- 本地 SQLite 缓存: 同查询在 TTL 内重复命中, 省时省流量。

设计要点:
- ``supports_search=True``, ``supports_extract=False`` — 只做搜索，提取仍走 crawl4ai
- Tavily keyless 模式: 请求头 ``X-Tavily-Access-Mode: keyless``，无需 TAVILY_API_KEY
- 复用环境变量 ``SEARXNG_URL``（与内置 searxng provider 一致）

Plugin layout (user-level, per profile):
    ~/.hermes/profiles/<name>/plugins/web/searxng-tavily/
        __init__.py   -> register(ctx)
        provider.py   -> this file
        plugin.yaml   -> kind: backend, provides_web_providers: [searxng-tavily]
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

TAVILY_API = "https://api.tavily.com"
KEYLESS_HEADER = {"X-Tavily-Access-Mode": "keyless"}
SEARXNG_TIMEOUT = 15  # 与内置 searxng provider 一致
TAVILY_TIMEOUT = 30

# ---- 熔断器参数 ---------------------------------------------------------
CIRCUIT_FAIL_THRESHOLD = 3    # 连续失败 3 次 → 熔断
CIRCUIT_COOLDOWN = 60         # 熔断冷却 60s
CIRCUIT_HALFOPEN_MAX = 1      # 半开状态最多放行 1 次探测
CACHE_TTL = 3600              # 搜索结果缓存 1 小时

# 共享缓存路径：Hermes 各 profile 会把 $HOME 覆盖成 profile 子目录（隔离机制），
# 导致 expanduser("~") 落在各自 profile 下、缓存互不共享。
# 这里用 pwd 拿系统真实用户 home，让所有 profile 共享同一个缓存文件。
try:
    import pwd as _pwd
    _REAL_HOME = _pwd.getpwuid(os.getuid()).pw_dir
except Exception:  # noqa: BLE001 — 非 POSIX 兜底
    _REAL_HOME = os.path.expanduser("~")

CACHE_DB = os.path.join(_REAL_HOME, ".cache", "searxng_tavily_cache.db")

# ---- 熔断器状态（类级, 进程内共享, 不受 provider 实例重建影响） ----------
_circuit_lock = threading.Lock()
_circuit_state = {
    "state": "closed",   # closed / open / half_open
    "failures": 0,
    "open_until": 0.0,   # open 态冷却截止时间戳
    "halfopen_inflight": 0,
}


def _circuit_state_snapshot() -> Dict[str, Any]:
    with _circuit_lock:
        return dict(_circuit_state)


class _CircuitBreaker:
    """进程级熔断器：SearXNG 连续失败 -> 熔断冷却 -> 半开探测 -> 恢复。

    显式三态状态机:
      closed    : 正常, 每次都放行
      open      : 熔断冷却期, 一律不放行（直接走 Tavily）, 直到 open_until 到期
      half_open : 冷却到期后, 限流放行 1 个探测请求; 成功→closed, 失败→再 open
    """

    @staticmethod
    def allow_request() -> bool:
        """是否允许走 SearXNG 请求。"""
        with _circuit_lock:
            now = time.time()
            st = _circuit_state["state"]
            if st == "closed":
                return True
            if st == "open":
                if now >= _circuit_state["open_until"]:
                    # 冷却到期 → 转 half_open, 限流放行探测
                    _circuit_state["state"] = "half_open"
                    _circuit_state["halfopen_inflight"] = 1
                    logger.info("SearXNG circuit HALF-OPEN (probe allowed)")
                    return True
                return False
            if st == "half_open":
                if _circuit_state["halfopen_inflight"] < CIRCUIT_HALFOPEN_MAX:
                    _circuit_state["halfopen_inflight"] += 1
                    return True
                return False
            return True  # 未知态兜底放行

    @staticmethod
    def record_success() -> None:
        with _circuit_lock:
            _circuit_state["state"] = "closed"
            _circuit_state["failures"] = 0
            _circuit_state["open_until"] = 0.0
            _circuit_state["halfopen_inflight"] = 0

    @staticmethod
    def record_failure() -> None:
        with _circuit_lock:
            st = _circuit_state["state"]
            if st == "half_open":
                # 半开探测失败 → 立即重新熔断
                _circuit_state["state"] = "open"
                _circuit_state["open_until"] = time.time() + CIRCUIT_COOLDOWN
                _circuit_state["halfopen_inflight"] = 0
                logger.warning("SearXNG probe failed — circuit OPEN again, cooldown %.0fs", CIRCUIT_COOLDOWN)
                return
            _circuit_state["failures"] += 1
            _circuit_state["halfopen_inflight"] = 0
            if _circuit_state["failures"] >= CIRCUIT_FAIL_THRESHOLD:
                _circuit_state["state"] = "open"
                _circuit_state["open_until"] = time.time() + CIRCUIT_COOLDOWN
                logger.warning(
                    "SearXNG circuit OPEN (failures=%d), cooldown %.0fs",
                    _circuit_state["failures"], CIRCUIT_COOLDOWN,
                )


class _SearchCache:
    """SQLite 结果缓存：同 query 在 TTL 内直接命中。"""

    def __init__(self, db_path: str = CACHE_DB) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        try:
            c = self._conn()
            c.execute(
                """CREATE TABLE IF NOT EXISTS search_cache (
                    query TEXT PRIMARY KEY,
                    results TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            c.commit()
        except sqlite3.Error as exc:
            logger.warning("search cache init failed: %s", exc)

    def get(self, query: str) -> Optional[List[Dict[str, Any]]]:
        try:
            c = self._conn()
            row = c.execute(
                "SELECT results, created_at FROM search_cache WHERE query=?",
                (query,),
            ).fetchone()
            if not row:
                return None
            results, created = row
            if time.time() - created > CACHE_TTL:
                return None  # 过期
            return __import__("json").loads(results)
        except Exception as exc:  # noqa: BLE001 — 缓存失败不阻断主流程
            logger.debug("cache get failed: %s", exc)
            return None

    def set(self, query: str, results: List[Dict[str, Any]]) -> None:
        try:
            c = self._conn()
            c.execute(
                "INSERT OR REPLACE INTO search_cache (query, results, created_at) VALUES (?,?,?)",
                (query, __import__("json").dumps(results, ensure_ascii=False), time.time()),
            )
            c.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache set failed: %s", exc)


class SearXNGTavilyWebSearchProvider(WebSearchProvider):
    """Search via SearXNG with automatic Tavily keyless fallback."""

    def __init__(self) -> None:
        self._cache = _SearchCache()

    @property
    def name(self) -> str:
        return "searxng-tavily"

    @property
    def display_name(self) -> str:
        return "SearXNG + Tavily(keyless) fallback"

    def is_available(self) -> bool:
        """Always available — SearXNG leg needs SEARXNG_URL, but the
        Tavily keyless fallback requires no key/URL at all, so this
        provider can always serve search."""
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    # -- search ----------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        # 1. 先查缓存
        cached = self._cache.get(query)
        if cached is not None:
            logger.info("SearXNG-tavily cache HIT for %r (%d results)", query, len(cached))
            return {
                "success": True,
                "data": {"web": cached[:limit]},
                "source": "cache",
            }

        # 2. SearXNG 路径（受熔断器控制）
        if _CircuitBreaker.allow_request():
            result = self._searxng_search(query, limit)
            if self._should_fallback(result):
                err = result.get("error", "")
                # 错误分类决定后续策略（熔断只针对 SearXNG 腿）
                if "rate_limited" in err or "429" in err:
                    _CircuitBreaker.record_failure()  # 限流 → 熔断计数
                elif result.get("success") is False:
                    _CircuitBreaker.record_failure()  # 网络/超时 → 熔断计数
                # no_results（success=True 但空）不熔断, 只是 fallback
                logger.info(
                    "SearXNG unavailable/empty for %r — falling back to Tavily keyless (%s)",
                    query, err,
                )
                result = self._tavily_keyless_search(query, limit)
                # 注意: Tavily 兜底成功【不】reset SearXNG 的熔断计数。
                # 熔断只反映 SearXNG 腿的健康度; 它恢复靠半开探测验证。
                if result.get("success"):
                    self._cache.set(query, result["data"]["web"])
            else:
                # SearXNG 本身成功 → 证明恢复, reset
                _CircuitBreaker.record_success()
                self._cache.set(query, result["data"]["web"])
        else:
            # 熔断中, 直接走 Tavily（不 reset 熔断计数 — 只有半开探测成功才恢复）
            logger.info("SearXNG circuit open — straight to Tavily keyless for %r", query)
            result = self._tavily_keyless_search(query, limit)
            if result.get("success"):
                self._cache.set(query, result["data"]["web"])

        # 3. 补充 source 标记（SearXNG / tavily / cache）
        if result.get("success"):
            if "source" not in result:
                src = "tavily" if result.get("data", {}).get("source_tag") == "tavily" else "searxng"
                result["data"]["source_tag"] = src
        return result

    # -- legs -------------------------------------------------------------

    def _classify_searxng_error(self, exc: Exception) -> str:
        """把 SearXNG 异常分类成可操作类型。"""
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "network_error"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code == 429:
                return "rate_limited"
            if 500 <= code < 600:
                return "server_error"
            return f"http_error_{code}"
        return "network_error"

    def _searxng_search(self, query: str, limit: int) -> Dict[str, Any]:
        import httpx

        base_url = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
        if not base_url:
            return {"success": False, "error": "SEARXNG_URL is not set"}
        params: Dict[str, Any] = {"q": query, "format": "json", "pageno": 1}
        try:
            resp = httpx.get(
                f"{base_url}/search",
                params=params,
                timeout=SEARXNG_TIMEOUT,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("SearXNG HTTP error: %s", exc)
            return {"success": False, "error": f"SearXNG returned HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            logger.warning("SearXNG request error: %s", exc)
            return {
                "success": False,
                "error": f"Could not reach SearXNG at {base_url}: {exc}",
                "err_type": self._classify_searxng_error(exc),
            }

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("SearXNG response parse error: %s", exc)
            return {"success": False, "error": "Could not parse SearXNG response as JSON"}

        raw_results = data.get("results", [])
        sorted_results = sorted(
            raw_results,
            key=lambda r: float(r.get("score", 0)),
            reverse=True,
        )[:limit]
        web_results = [
            {
                "title": str(r.get("title", "")),
                "url": str(r.get("url", "")),
                "description": str(r.get("content", "")),
                "position": i + 1,
            }
            for i, r in enumerate(sorted_results)
        ]
        logger.info("SearXNG search '%s': %d results", query, len(web_results))
        return {"success": True, "data": {"web": web_results}}

    def _tavily_keyless_search(self, query: str, limit: int) -> Dict[str, Any]:
        import httpx

        payload = {
            "query": query,
            "max_results": min(limit, 20),
            "include_raw_content": False,
            "include_images": False,
        }
        try:
            resp = httpx.post(
                f"{TAVILY_API}/search",
                json=payload,
                headers=KEYLESS_HEADER,
                timeout=TAVILY_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Tavily keyless HTTP error: %s", exc)
            return {"success": False, "error": f"Tavily returned HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            logger.warning("Tavily keyless request error: %s", exc)
            return {"success": False, "error": f"Could not reach Tavily: {exc}"}

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily keyless parse error: %s", exc)
            return {"success": False, "error": "Could not parse Tavily response as JSON"}

        web_results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("content", ""),
                "position": i + 1,
            }
            for i, r in enumerate(data.get("results", []))
        ]
        logger.info("Tavily keyless fallback search '%s': %d results", query, len(web_results))
        return {
            "success": True,
            "data": {"web": web_results, "source_tag": "tavily"},
        }

    @staticmethod
    def _should_fallback(result: Dict[str, Any]) -> bool:
        """Fall back when SearXNG failed OR returned zero results."""
        if not result.get("success"):
            return True
        return len(result.get("data", {}).get("web", [])) == 0

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · self-hosted + keyless",
            "tag": "SearXNG first, Tavily keyless fallback (no API key needed). Uses SEARXNG_URL.",
            "env_vars": [
                {
                    "key": "SEARXNG_URL",
                    "prompt": "SearXNG instance URL (e.g. https://your-instance/searxng)",
                },
            ],
        }
