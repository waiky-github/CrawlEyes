#!/usr/bin/env python3
"""一键回归测试：按固定顺序运行所有测试脚本，汇总结果。

用法:
    python scripts/run_tests.py            # 跑全部
    python scripts/run_tests.py test_core  # 只跑指定测试（支持多个名字）

退出码: 0 = 全部通过, 1 = 有失败（CI 可用）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 仓库根目录
ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"

# 测试文件（按依赖/重要性排序）
TEST_FILES = [
    "test_core.py",           # 核心逻辑 + MCP 工具注册（6 项）
    "test_rate_limit.py",     # 统一限流（8 项）
    "test_robots.py",         # robots.txt 可选遵守（4 项）
    "test_sitemap.py",        # sitemap 发现（6 项）
    "test_firecrawl_api.py",  # firecrawl 兼容 /scrape 协议（6 项）
]


def run_one(name: str, python: str) -> bool:
    """运行单个测试文件，返回是否通过。"""
    test_file = TESTS_DIR / name
    if not test_file.exists():
        print(f"  ❌ {name}: 文件不存在")
        return False
    print(f"\n=== {name} ===")
    try:
        r = subprocess.run([python, str(test_file)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=180)
        print(r.stdout.rstrip())
        if r.stdout:
            # 显示最后一行（N/M passed 或 exit 标记）
            last = [l for l in r.stdout.splitlines() if l.strip()][-1:]
            for l in last:
                print(f"  → {l}")
        if r.returncode != 0:
            print(f"  ❌ {name} 失败 (exit={r.returncode})")
            if r.stderr:
                print(r.stderr[-500:])
            return False
        print(f"  ✅ {name} 通过")
        return True
    except subprocess.TimeoutExpired:
        print(f"  ❌ {name} 超时")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {name} 运行异常: {e}")
        return False


def main() -> int:
    # 用仓库 venv 的 python（优先），否则用系统 python
    venv_python = ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable

    args = sys.argv[1:]
    targets = [a for a in args if a.endswith(".py")] or TEST_FILES
    targets = [t if t.endswith(".py") else f"{t}.py" for t in targets]

    print(f"CrawlEyes 测试回归 ({python})")
    print(f"目标: {len(targets)} 个测试文件")

    results = {t: run_one(t, python) for t in targets}
    passed = sum(1 for v in results.values() if v)

    print(f"\n{'='*40}")
    print(f"结果: {passed}/{len(results)} 个测试文件通过")
    for t, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {t}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
