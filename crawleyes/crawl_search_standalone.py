#!/usr/bin/env python3
"""
独立版搜索实现（供 MCP server 使用，不依赖 Hermes agent 包）
==============================================================
SearXNG 优先 → Tavily keyless 兜底。零 API key，可在任何 Python 环境运行。

与 plugins/searxng-tavily/provider.py 的区别:
- 去掉 WebSearchProvider 基类依赖（那是 Hermes 插件接口）
- 保留核心: SearXNG 查询 + Tavily keyless 兜底 + 简单缓存
- 供 mcp_crawl_server.py 和独立脚本调用
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TAVILY_API = "https://api.tavily.com"
KEYLESS_HEADER = {"X-Tavily-Access-Mode": "keyless"}
SEARXNG_TIMEOUT = 15
TAVILY_TIMEOUT = 30
CACHE_TTL = 3600

try:
    import pwd as _pwd
    _REAL_HOME = _pwd.getpwuid(os.getuid()).pw_dir
except Exception:  # noqa: BLE001
    _REAL_HOME = os.path.expanduser("~")
CACHE_DB = os.path.join(_REAL_HOME, ".cache", "searxng_tavily_cache.db")


class _Cache:
    def __init__(self, db_path: str = CACHE_DB):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db = db_path
        self._local = threading.local()
        self._init()

    def _conn(self):
        if not hasattr(self._local, "conn"):
            c = sqlite3.connect(self._db, timeout=5)
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return self._local.conn

    def _init(self):
        try:
            c = self._conn()
            c.execute("""CREATE TABLE IF NOT EXISTS search_cache (
                query TEXT PRIMARY KEY, results TEXT NOT NULL, created_at REAL NOT NULL)""")
            c.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("cache init failed: %s", e)

    def get(self, query: str) -> Optional[List[Dict[str, Any]]]:
        try:
            row = self._conn().execute(
                "SELECT results, created_at FROM search_cache WHERE query=?",
                (query,)).fetchone()
            if not row:
                return None
            results, created = row
            if time.time() - created > CACHE_TTL:
                return None
            return json.loads(results)
        except Exception:  # noqa: BLE001
            return None

    def set(self, query: str, results: List[Dict[str, Any]]):
        try:
            self._conn().execute(
                "INSERT OR REPLACE INTO search_cache VALUES (?,?,?)",
                (query, json.dumps(results, ensure_ascii=False), time.time()))
            self._conn().commit()
        except Exception:  # noqa: BLE001
            pass


class CrawlSearch:
    """独立搜索：SearXNG 优先，Tavily keyless 兜底。"""

    def __init__(self, rerank: bool = False):
        self._cache = _Cache()
        self._rerank = rerank
        self._reranker = None

    def search(self, query: str, limit: int = 5, rerank: bool | None = None) -> Dict[str, Any]:
        do_rerank = self._rerank if rerank is None else rerank
        cached = self._cache.get(query)
        if cached is not None:
            return {"success": True, "data": {"web": cached[:limit]}, "source": "cache"}

        result = self._searxng(query, limit)
        if not result.get("success") or len(result.get("data", {}).get("web", [])) == 0:
            source = "tavily"
            result = self._tavily(query, limit)
        else:
            source = "searxng"
        if result.get("success"):
            web = result["data"]["web"]
            if do_rerank and web:
                web = self._rerank_results(query, web)
            self._cache.set(query, web)
            result["data"]["web"] = web
            result["data"]["source_tag"] = source
        return result

    # ---- P2: 本地嵌入语义重排 (fastembed + bge-small-zh, 不依赖 torch) ----
    def _get_reranker(self):
        if self._reranker is None:
            # 国内网络: hf-mirror + 禁用 xet (hf-mirror 不支持 xet 协议)
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from fastembed import TextEmbedding
            self._reranker = TextEmbedding("BAAI/bge-small-zh-v1.5")
        return self._reranker

    def _rerank_results(self, query: str, web: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            import numpy as np
            model = self._get_reranker()
            # query 嵌入
            q_emb = list(model.embed([query]))[0]
            # 每个结果用 title+description 嵌入
            texts = [f"{r.get('title','')} {r.get('description','')}" for r in web]
            embs = list(model.embed(texts))
            qv = np.array(q_emb)
            scores = []
            for e in embs:
                ev = np.array(e)
                denom = (np.linalg.norm(qv) * np.linalg.norm(ev)) or 1.0
                scores.append(float(qv @ ev / denom))
            # 按相似度降序, 返回带 score 的结果
            ranked = sorted(zip(web, scores), key=lambda x: -x[1])
            out = []
            for i, (r, s) in enumerate(ranked):
                r = dict(r)
                r["score"] = round(s, 4)
                r["position"] = i + 1
                out.append(r)
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("rerank failed (fallback to original order): %s", e)
            return web

    def _searxng(self, query: str, limit: int) -> Dict[str, Any]:
        import httpx
        base_url = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
        if not base_url:
            return {"success": False, "error": "SEARXNG_URL not set"}
        try:
            resp = httpx.get(f"{base_url}/search", params={"q": query, "format": "json"},
                             timeout=SEARXNG_TIMEOUT, headers={"Accept": "application/json"})
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.info("SearXNG unavailable: %s", e)
            return {"success": False, "error": str(e)}
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"parse: {e}"}
        web = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "description": r.get("content", ""), "position": i + 1}
            for i, r in enumerate(sorted(data.get("results", []),
                                         key=lambda x: float(x.get("score", 0)), reverse=True)[:limit])
        ]
        return {"success": True, "data": {"web": web}}

    def _tavily(self, query: str, limit: int) -> Dict[str, Any]:
        import httpx
        try:
            resp = httpx.post(f"{TAVILY_API}/search",
                              json={"query": query, "max_results": min(limit, 20)},
                              headers=KEYLESS_HEADER, timeout=TAVILY_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"parse: {e}"}
        web = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "description": r.get("content", ""), "position": i + 1}
            for i, r in enumerate(data.get("results", []))
        ]
        return {"success": True, "data": {"web": web}}
