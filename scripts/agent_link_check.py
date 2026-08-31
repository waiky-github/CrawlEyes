#!/usr/bin/env python3
"""真实 agent 链路验证：对单个 Hermes profile，以网关同款方式加载插件，
再经 tools.web_tools.web_search_tool 真实调用，核对：
  1. registry 里 searxng-tavily provider 已注册且 is_available
  2. _get_search_backend() 返回 searxng-tavily
  3. _get_extract_backend() 返回 crawl4ai
  4. web_search_tool 真实返回结果（证明工具层 dispatch 走通）
  5. 日志出现 'Web search via searxng-tavily'（真实 agent 记录点）
用法: venv/bin/python /path/agent_link_check.py <HERMES_HOME>
"""
import os
import sys
import json
import logging
import io

def main():
    hermes_home = sys.argv[1] if len(sys.argv) > 1 else ""
    if not hermes_home or not os.path.isdir(hermes_home):
        print("用法: agent_link_check.py <HERMES_HOME>")
        sys.exit(2)
    os.environ["HERMES_HOME"] = hermes_home
    os.environ["HERMES_PROFILE"] = os.path.basename(hermes_home.rstrip("/"))

    # 加载 .env (模拟 hermes 启动, 需 SEARXNG_URL / 各 key)
    from pathlib import Path
    env_path = Path(hermes_home) / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    # 记录 web_tools 日志输出（"Web search via ..." 来自 tools.web_tools logger）
    from hermes_logging import setup_logging
    setup_logging()

    # 同网关：发现并加载启用插件 (discover_plugins == get_plugin_manager().discover_and_load)
    from hermes_cli.plugins import discover_plugins
    discover_plugins(force=False)

    # registry 检查
    from agent.web_search_registry import get_provider
    p = get_provider("searxng-tavily")
    print(f"[1] searxng-tavily registered: {p is not None}")
    if p is None:
        print("    FAIL: provider 未注册"); sys.exit(1)
    print(f"    is_available: {p.is_available()}  supports_search: {p.supports_search()}")

    from tools.web_tools import _get_search_backend, _get_extract_backend, _is_backend_available
    sb = _get_search_backend()
    eb = _get_extract_backend()
    print(f"[2] search_backend = {sb}  (expect searxng-tavily)")
    print(f"[3] extract_backend = {eb}  (expect crawl4ai)")
    print(f"    is_available(search)={_is_backend_available(sb)}  is_available(extract)={_is_backend_available(eb)}")
    if sb != "searxng-tavily":
        print("    FAIL: search_backend 不是 searxng-tavily"); sys.exit(1)
    if eb != "crawl4ai":
        print("    FAIL: extract_backend 不是 crawl4ai"); sys.exit(1)

    # 真实工具调用（同步，不能 await）
    from tools.web_tools import web_search_tool
    print("[4] 真实 web_search_tool('Crawl4AI 是什么') ...")
    try:
        res = web_search_tool("Crawl4AI 是什么", limit=3)
        data = json.loads(res) if isinstance(res, str) else res
        ok = data.get("success")
        results = (data.get("data") or {}).get("web") or []
        print(f"    success={ok}  results={len(results)}")
        for r in results[:2]:
            print(f"      - {r.get('title','')[:60]}")
        if not ok or not results:
            print("    FAIL: 搜索无结果"); sys.exit(1)
    except Exception as e:
        print(f"    FAIL: web_search_tool 异常: {type(e).__name__}: {e}")
        sys.exit(1)

    print("[5] PASS: 链路全通 (searxng-tavily 搜索可用)")

if __name__ == "__main__":
    main()
