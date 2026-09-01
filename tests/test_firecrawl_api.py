"""Offline protocol tests for the Firecrawl-compatible /scrape endpoint.

Covers the HTTP contract (routes, status codes, JSON shape) without external
network: the scrape engine is mocked, so tests are deterministic in CI.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawleyes import firecrawl_api


class _ScrapeEngine:
    """可替换的 scrape 引擎（测试用 mock）。"""


def _patch_scrape(mock_result):
    """把 firecrawl_api._scrape_sync 替换为返回 mock_result 的函数。"""
    def fake(url, timeout=30, max_words=0):
        return dict(mock_result)
    firecrawl_api._scrape_sync = fake


def _start_server(port):
    server = firecrawl_api.ThreadingHTTPServer(("127.0.0.1", port), firecrawl_api.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


PORT = 18999  # 测试专用端口，避开默认 8899


def test_healthz():
    server = _start_server(PORT)
    try:
        status, body = _get(PORT, "/healthz")
        assert status == 200, f"expected 200, got {status}"
        assert body["status"] == "ok"
        assert "crawleyes" in body["service"]
    finally:
        server.shutdown()


def test_missing_url_returns_400():
    server = _start_server(PORT)
    try:
        status, body = _post(PORT, "/v2/scrape", {})
        assert status == 400, f"expected 400, got {status}"
        assert body["success"] is False
        assert "url" in body["error"]
    finally:
        server.shutdown()


def test_invalid_json_returns_400():
    server = _start_server(PORT)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/v2/scrape",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body = json.loads(e.read().decode("utf-8"))
            assert body["success"] is False
    finally:
        server.shutdown()


def test_unknown_path_returns_404():
    server = _start_server(PORT)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/nope", method="GET")
        try:
            urllib.request.urlopen(req, timeout=10)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


def test_scrape_success_contract():
    """成功路径: 返回 firecrawl v2 契约 { success, data: { markdown, metadata } }。"""
    _patch_scrape({"success": True, "markdown": "# Hello", "title": "Test", "url": "https://example.com"})
    server = _start_server(PORT)
    try:
        status, body = _post(PORT, "/v2/scrape", {"url": "https://example.com"})
        assert status == 200, f"expected 200, got {status}"
        assert body["success"] is True
        assert body["data"]["markdown"] == "# Hello"
        assert body["data"]["metadata"]["title"] == "Test"
        assert body["data"]["metadata"]["url"] == "https://example.com"
    finally:
        server.shutdown()


def test_scrape_failure_contract():
    """失败路径: 引擎返回 success=False → 502 + error。"""
    _patch_scrape({"success": False, "error": "boom", "url": "https://example.com"})
    server = _start_server(PORT)
    try:
        status, body = _post(PORT, "/v2/scrape", {"url": "https://example.com"})
        assert status == 502, f"expected 502, got {status}"
        assert body["success"] is False
        assert "boom" in body["error"]
    finally:
        server.shutdown()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
