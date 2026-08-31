#!/usr/bin/env python3
"""逐环境验证 searxng-tavily provider 端到端。

用法: python verify_searxng_tavily.py <HERMES_HOME>
验证: 1) 加载 .env 2) provider 注册+available 3) SearXNG 正常搜索
      4) 强制 fallback: 把 SEARXNG_URL 指向不通地址, 验证自动切 Tavily keyless
"""
import os, sys, json, traceback
from pathlib import Path

hermes_home = sys.argv[1]
os.environ["HERMES_HOME"] = hermes_home
os.environ["HERMES_PROFILE"] = os.path.basename(hermes_home.rstrip("/"))

def dumps(o):
    return json.dumps(o, ensure_ascii=False)

# 加载 .env (模拟 hermes 启动)
env_path = Path(hermes_home) / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

out = {"ok": False, "steps": []}
try:
    from hermes_cli.plugins import discover_plugins
    from agent.web_search_registry import get_provider
    discover_plugins(force=False)
    prov = get_provider("searxng-tavily")
    if prov is None:
        out["steps"].append("provider 未注册 ❌")
        print(dumps(out)); sys.exit(1)
    out["steps"].append(f"provider 已注册, is_available={prov.is_available()}")
    if not prov.is_available():
        out["steps"].append("is_available=False ❌")
        print(dumps(out)); sys.exit(1)

    # 1. SearXNG 正常路径
    r1 = prov.search("Hermes agent", limit=3)
    n1 = len(r1.get("data", {}).get("web", [])) if r1.get("success") else -1
    out["steps"].append(f"searxng路径: success={r1.get('success')} results={n1}")
    if not r1.get("success") or n1 == 0:
        out["steps"].append(f"searxng路径失败: {r1.get('error')}")

    # 2. 强制 fallback
    os.environ["SEARXNG_URL"] = "http://127.0.0.1:1"
    r2 = prov.search("open source search engine", limit=3)
    n2 = len(r2.get("data", {}).get("web", [])) if r2.get("success") else -1
    out["steps"].append(f"fallback路径: success={r2.get('success')} results={n2} (样本: {(r2.get('data',{}).get('web',[{}])[0].get('title','') if n2 else '')[:40]})")
    if r2.get("success") and n2 > 0:
        out["ok"] = True
    else:
        out["steps"].append(f"fallback失败: {r2.get('error')}")
    print(dumps(out))
except Exception:
    out["steps"].append("EXC: " + traceback.format_exc()[-500:])
    print(dumps(out))
    sys.exit(1)
