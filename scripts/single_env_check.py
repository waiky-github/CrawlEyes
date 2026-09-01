#!/usr/bin/env python3
"""单环境干净子进程验证：crawl4ai provider 是否注册 + 可用 + 能抓取。

由 verify_all_envs.sh 以独立子进程调用，避免同进程 reload 的 class identity 问题。
用法: python single_env_check.py <HERMES_HOME>
"""
import asyncio
import json
import os
import sys
import traceback


def json_dumps(o):
    return json.dumps(o, ensure_ascii=False)

hermes_home = sys.argv[1]
os.environ["HERMES_HOME"] = hermes_home
os.environ["HERMES_PROFILE"] = os.path.basename(hermes_home.rstrip("/"))

result = {"ok": False, "steps": []}
try:
    from agent.web_search_registry import get_provider, list_providers
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins(force=False)
    manager = get_plugin_manager()
    result["steps"].append(f"plugins_loaded={len(manager.list_plugins())}")

    names = [getattr(x, "name", "?") for x in list_providers()]
    result["steps"].append(f"registered_providers={names}")

    prov = get_provider("crawl4ai")
    if prov is None:
        result["steps"].append("provider=crawl4ai 未注册 ❌")
        print(json_dumps(result))
        sys.exit(1)

    result["steps"].append(f"provider={getattr(prov,'name','?')}")
    avail = prov.is_available()
    result["steps"].append(f"is_available={avail}")
    if not avail:
        result["steps"].append("is_available=False ❌")
        print(json_dumps(result))
        sys.exit(1)

    async def do_extract():
        return await prov.extract(["https://example.com"])
    res = asyncio.run(do_extract())
    item = res[0] if res else {}
    if item.get("error"):
        result["steps"].append(f"extract_error={item['error'][:200]} ❌")
        print(json_dumps(result))
        sys.exit(1)
    result["steps"].append(f"extract_ok title={item.get('title','')!r} content_len={len(item.get('content',''))}")
    result["ok"] = True
    print(json_dumps(result))
except Exception:
    result["steps"].append("EXC: " + traceback.format_exc()[-500:])
    print(json_dumps(result))
    sys.exit(1)
