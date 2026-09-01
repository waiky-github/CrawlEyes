#!/usr/bin/env python3
"""
Firecrawl-compatible /scrape endpoint for CrawlEyes
===================================================
提供 Firecrawl SDK 风格的单页抓取 REST 端点，让"已经用 Firecrawl 的脚本/agent"
可以通过改 API URL 直接切换到 CrawlEyes，无需改代码。

兼容范围（P3-3，务实版）:
  - POST /v2/scrape     : 抓取单页，返回 { success, data: { markdown, metadata } }
  - GET  /healthz       : 健康检查

设计说明:
  - 只做核心 /scrape 单端点（firecrawl SDK 最常用的能力），不承诺全端点兼容
    （/crawl 异步任务队列 /search /map 等不在范围 —— 见 P2-A 评估）。
  - 用标准库 http.server，零新依赖；复用 crawleyes/crawl4ai_cli.scrape()。
  - 响应字段对齐 firecrawl v2 SDK 契约: { success: bool, data: { markdown, metadata } }
  - 默认监听 127.0.0.1:8899（纯本地，不自带鉴权 —— 部署到公网前需加代理/密钥）。

用法:
  python -m crawleyes.firecrawl_api --port 8899 --host 127.0.0.1

  # Firecrawl 用户切换（Python SDK 示例）:
  #   from firecrawl import Firecrawl
  #   fc = Firecrawl(api_url="http://127.0.0.1:8899", api_key="ignored")
  #   doc = fc.scrape(url="https://example.com")   # → { markdown, metadata }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def _scrape_sync(url: str, timeout: int, max_words: int) -> dict:
    """同步包装 async scrape()（http.server handler 是同步的）。"""
    from .crawl4ai_cli import scrape

    return asyncio.run(scrape(url, timeout=timeout, max_words=max_words))


class Handler(BaseHTTPRequestHandler):
    server_version = "CrawlEyes/0.2"

    def log_message(self, format, *args):  # 安静模式，不刷日志
        return

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/healthz", "/health"):
            self._send_json(200, {"status": "ok", "service": "crawleyes-firecrawl"})
            return
        self._send_json(404, {"success": False, "error": f"not found: {path}"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/v2/scrape", "/v0/scrape"):
            self._send_json(404, {"success": False, "error": f"not found: {path}"})
            return

        # 读取请求体
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"success": False, "error": f"invalid JSON body: {e}"})
            return

        url = (req.get("url") or "").strip()
        if not url:
            self._send_json(400, {"success": False, "error": "missing 'url' in request body"})
            return

        # 兼容 firecrawl options（取常用项）
        timeout = int(req.get("timeout", 30000)) // 1000 or 30  # ms → s
        max_words = int(req.get("maxWords", 0) or req.get("max_words", 0) or 0)

        try:
            result = _scrape_sync(url, timeout=timeout, max_words=max_words)
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"success": False, "error": f"scrape failed: {e}"})
            return

        if not result.get("success"):
            self._send_json(502, {
                "success": False,
                "error": result.get("error", "scrape failed"),
                "url": url,
            })
            return

        # firecrawl v2 契约: { success, data: { markdown, metadata } }
        self._send_json(200, {
            "success": True,
            "data": {
                "markdown": result.get("markdown", ""),
                "metadata": {
                    "title": result.get("title"),
                    "url": result.get("url", url),
                },
            },
        })


def main():
    parser = argparse.ArgumentParser(description="CrawlEyes Firecrawl-compatible /scrape endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CrawlEyes Firecrawl-compatible API: http://{args.host}:{args.port}")
    print("  POST /v2/scrape  →  { success, data: { markdown, metadata } }")
    print("  GET  /healthz    →  健康检查")
    print("(Ctrl+C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
