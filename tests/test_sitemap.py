"""Offline tests for sitemap discovery (P1-3).

Deterministic: serves sitemap.xml / sitemap index / gzip / robots.txt from an
in-process HTTP server — no external network.
"""
import gzip
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crawleyes.sitemap import discover_sitemap_urls, parse_sitemap


class _SitemapHandler(BaseHTTPRequestHandler):
    routes: ClassVar[dict[str, bytes]] = {}

    def do_GET(self):
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _serve(routes: dict[str, bytes], port: int):
    _SitemapHandler.routes = routes
    srv = HTTPServer(("127.0.0.1", port), _SitemapHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_parse_plain_sitemap():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/a</loc></url>
      <url><loc>https://example.com/b</loc></url>
    </urlset>"""
    srv = _serve({"/sitemap.xml": xml}, 18081)
    try:
        urls = parse_sitemap("http://127.0.0.1:18081/sitemap.xml")
        assert urls == ["https://example.com/a", "https://example.com/b"], urls
    finally:
        srv.shutdown()


def test_parse_sitemap_index_recursion():
    index = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>http://127.0.0.1:18082/part1.xml</loc></sitemap>
      <sitemap><loc>http://127.0.0.1:18082/part2.xml</loc></sitemap>
    </sitemapindex>"""
    part1 = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>http://x/p1</loc></url></urlset>"""
    part2 = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>http://x/p2</loc></url></urlset>"""
    routes = {
        "/sitemap.xml": index,
        "/part1.xml": part1,
        "/part2.xml": part2,
    }
    srv = _serve(routes, 18082)
    try:
        urls = parse_sitemap("http://127.0.0.1:18082/sitemap.xml")
        assert "http://x/p1" in urls and "http://x/p2" in urls, urls
    finally:
        srv.shutdown()


def test_parse_gzip_sitemap():
    xml = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/gz</loc></url></urlset>"""
    gz = gzip.compress(xml)
    srv = _serve({"/sitemap.xml": gz}, 18083)
    try:
        urls = parse_sitemap("http://127.0.0.1:18083/sitemap.xml")
        assert urls == ["https://example.com/gz"], urls
    finally:
        srv.shutdown()


def test_discover_falls_back_to_robots_txt():
    # no sitemap.xml, but robots.txt declares a Sitemap
    xml = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/from-robots</loc></url></urlset>"""
    robots = b"User-agent: *\nDisallow: /private/\nSitemap: http://127.0.0.1:18084/custom.xml\n"
    routes = {"/custom.xml": xml, "/robots.txt": robots}
    srv = _serve(routes, 18084)
    try:
        urls = discover_sitemap_urls("http://127.0.0.1:18084")
        assert urls == ["https://example.com/from-robots"], urls
    finally:
        srv.shutdown()


def test_discover_empty_on_nothing():
    srv = _serve({}, 18085)
    try:
        urls = discover_sitemap_urls("http://127.0.0.1:18085")
        assert urls == [], urls
    finally:
        srv.shutdown()


def test_dedupes_urls():
    xml = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/dup</loc></url>
      <url><loc>https://example.com/dup</loc></url>
    </urlset>"""
    srv = _serve({"/sitemap.xml": xml}, 18086)
    try:
        urls = parse_sitemap("http://127.0.0.1:18086/sitemap.xml")
        assert urls == ["https://example.com/dup"], urls
    finally:
        srv.shutdown()
