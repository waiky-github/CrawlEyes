#!/usr/bin/env python3
"""
Crawl4AI 通用抓取 CLI —— 供所有 Hermes agent 复用
=====================================================
抓任意 URL → 转干净 Markdown / 纯文本，输出到 stdout 或文件。

v1.1 新增（2026-08-31）:
- --noise-filter : 正文去噪（PruningContentFilter 修剪导航/广告/评论区, 借鉴
                   readability/GeneralNewsExtractor 思路, 用 Crawl4AI 原生实现）
- --bm25 关键字  : BM25 内容过滤, 只保留与查询相关的段落（借鉴 Vane 的检索重排思路）
- --retry N      : 指数退避重试（借鉴 Crawlee: 1s/2s/4s, 默认 2 次）
- --session NAME : 浏览器 session 复用, 避免每次冷启动（借鉴 camofox 会话持久化）

用法:
    .venv/bin/python scripts/crawl4ai_cli.py URL [选项]

选项:
    -o FILE         输出到文件 (默认 stdout)
    --max-words N   截断到 N 词 (默认 20000, 0=不限)
    --timeout N     页面超时秒 (默认 30)
    --text          只输出纯文本 (去掉 Markdown 符号)
    --js            执行自定义 JS 后再抓 (如滚动加载)
    --noise-filter  正文去噪 (修剪导航/侧栏/广告/评论区噪音)
    --bm25 KEYWORD  只保留与关键词相关的段落 (可多次)
    --retry N       失败重试次数 (默认 2, 0=不重试)
    --session NAME  复用浏览器 session (同一进程内多次抓取)

示例:
    .venv/bin/python scripts/crawl4ai_cli.py https://example.com --noise-filter
    .venv/bin/python scripts/crawl4ai_cli.py https://news.example --bm25 "AI 芯片" --retry 3
"""
import argparse
import asyncio
import json
import logging

# 默认安静, 只在重试/错误时打日志
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("crawl4ai_cli")


async def scrape(url, timeout=30, max_words=0, text_only=False, js_code=None,
                 noise_filter=False, bm25_keywords=None, retry=2, session=None):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import BM25ContentFilter, PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    browser_cfg = BrowserConfig(
        headless=True,
        user_agent_mode="random",     # 随机真实 UA
        java_script_enabled=True,
    )

    # P1: 内容过滤策略（去噪）
    content_filter = None
    if noise_filter:
        content_filter = PruningContentFilter(
            threshold=0.45,        # 低于该密度分的块被修剪（导航/广告密度低）
            threshold_type="fixed",
            min_word_threshold=2,  # 少于2词的块丢弃
        )
        if bm25_keywords:
            # BM25: 在去噪基础上, 只保留与查询相关的段落
            # 中文场景阈值放低 (默认1.0), 避免过度过滤只留几行
            content_filter = BM25ContentFilter(
                user_query=" ".join(bm25_keywords),
                bm25_threshold=0.8,
                language="english",  # crawl4ai BM25 对中文也按 token 切, 阈值已放宽
            )
    markdown_gen = DefaultMarkdownGenerator(content_filter=content_filter) if content_filter else None

    run_cfg = CrawlerRunConfig(
        wait_until="domcontentloaded",
        page_timeout=timeout * 1000,
        verbose=False,
        markdown_generator=markdown_gen,
    )
    if js_code:
        run_cfg.js_code = js_code
    if session:
        run_cfg.session_id = session  # P4: 会话复用

    # P3: 指数退避重试
    attempt = 0
    while True:
        attempt += 1
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
                if result.success:
                    # 优先取过滤后的 fit_markdown（有 content_filter 时生成）
                    # 新版 API: result.markdown.fit_markdown; 旧版: result.fit_markdown
                    md = ""
                    markdown_obj = getattr(result, "markdown", None)
                    if isinstance(markdown_obj, dict):
                        md = markdown_obj.get("fit_markdown") or markdown_obj.get("markdown") or ""
                    elif hasattr(markdown_obj, "fit_markdown"):
                        md = markdown_obj.fit_markdown or ""
                    if not md:
                        md = getattr(result, "fit_markdown", "") or ""
                    if not md:
                        md = getattr(result, "markdown", "") or ""
                        if not isinstance(md, str):
                            md = str(md)
                    if text_only:
                        import re
                        md = re.sub(r'[#>*`_\-\[\]()!]', '', md)
                        md = re.sub(r'\n{3,}', '\n\n', md)
                    if max_words > 0:
                        words = md.split()
                        if len(words) > max_words:
                            md = ' '.join(words[:max_words]) + '\n...[截断]'
                    meta = result.metadata or {}
                    return {
                        "success": True,
                        "title": getattr(meta, 'title', None) or (meta.get('title') if isinstance(meta, dict) else None),
                        "url": url,
                        "length": len(md),
                        "markdown": md,
                    }
                err = result.error_message or "未知错误"
        except Exception as exc:  # noqa: BLE001 — 抓取失败原因多样
            err = f"{type(exc).__name__}: {exc}"

        if attempt > retry:
            log.warning("抓取失败(已重试%d次): %s", attempt - 1, err)
            return {"success": False, "error": err, "url": url, "attempts": attempt - 1}

        # 指数退避: 1s / 2s / 4s ...
        backoff = 2 ** (attempt - 1)
        log.warning("第%d次尝试失败: %s — %.1fs后重试", attempt, err, backoff)
        await asyncio.sleep(backoff)


def main():
    parser = argparse.ArgumentParser(description="Crawl4AI 通用抓取 CLI")
    parser.add_argument("url", help="目标 URL 或搜索关键词")
    parser.add_argument("-o", "--output", help="输出文件路径")
    parser.add_argument("--max-words", type=int, default=20000, help="截断词数 (0=不限)")
    parser.add_argument("--timeout", type=int, default=30, help="页面超时秒")
    parser.add_argument("--text", action="store_true", help="只输出纯文本")
    parser.add_argument("--js", help="执行自定义 JS 后再抓 (如滚动加载)")
    parser.add_argument("--noise-filter", action="store_true", help="正文去噪 (修剪导航/广告/评论区)")
    parser.add_argument("--bm25", action="append", default=None, metavar="KEYWORD", help="只保留与关键词相关的段落 (可多次)")
    parser.add_argument("--retry", type=int, default=2, help="失败重试次数 (默认2, 0=不重试)")
    parser.add_argument("--session", help="复用浏览器 session (同一进程内多次抓取)")
    args = parser.parse_args()

    result = asyncio.run(scrape(
        args.url, args.timeout, args.max_words, args.text, args.js,
        args.noise_filter, args.bm25, args.retry, args.session,
    ))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result.get("markdown", "") or result.get("error", ""))
        print(f"# 已写入: {args.output} ({result.get('length', 0)} 字)")
        print(json.dumps({"success": result["success"], "length": result.get("length", 0),
                          "title": result.get("title"), "file": args.output},
                         ensure_ascii=False))
    else:
        if result.get("success"):
            print(f"\n# 标题: {result.get('title')}")
            print(f"# 字数: {result.get('length')}")
            print("#" * 40)
            print(result.get("markdown", ""))
        else:
            print(f"# 抓取失败: {result.get('error')}")
            if result.get("markdown"):
                print(result["markdown"][:500])


if __name__ == "__main__":
    main()
