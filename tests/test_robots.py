"""Offline tests for robots.txt compliance (P0-2) and format handling (P0-1).

These are deterministic — they use a local in-process HTTP server, so they
run in CI with no external network.
"""
import asyncio
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _RobotsHandler(BaseHTTPRequestHandler):
    robots_body = b"User-agent: *\nDisallow: /admin/\n"

    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(self.robots_body)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"page")

    def log_message(self, format, *args):  # noqa: A002, N802
        pass


class _NoRobotsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"page")

    def log_message(self, format, *args):  # noqa: A002, N802
        pass


def _start(handler_cls, port):
    srv = HTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_robots_allows_public_path():
    from crawleyes.robots import check_robots

    srv = _start(_RobotsHandler, 18091)
    try:
        ok, _ = asyncio.run(check_robots("http://127.0.0.1:18091/public"))
        assert ok is True
    finally:
        srv.shutdown()


def test_robots_blocks_disallowed_path():
    from crawleyes.robots import check_robots

    srv = _start(_RobotsHandler, 18092)
    try:
        ok, reason = asyncio.run(check_robots("http://127.0.0.1:18092/admin/settings"))
        assert ok is False
        assert "disallowed" in reason
    finally:
        srv.shutdown()


def test_robots_fail_open_on_no_robots():
    from crawleyes.robots import check_robots

    srv = _start(_NoRobotsHandler, 18093)
    try:
        ok, _ = asyncio.run(check_robots("http://127.0.0.1:18093/anything"))
        assert ok is True
    finally:
        srv.shutdown()


def test_robots_fail_open_on_invalid_url():
    from crawleyes.robots import check_robots

    ok, _ = asyncio.run(check_robots("not-a-url"))
    assert ok is True
