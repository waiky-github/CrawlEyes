# 给 AI Agents 一双「眼睛」：CrawlEyes 开源了

> 抓网页、搜全网、按语义重排、一键接入 Agent——一个开源工具箱全搞定。

## 背景：AI Agent 普遍「看不见网页」

做 AI Agent 的人都撞过同一堵墙：模型再聪明，也读不到实时网页。要让 Agent 真正干活，通常要自己拼一堆东西——

- 抓网页（headless 浏览器、绕过 JS 渲染、清洗正文）
- 搜索（接 API、配 key、处理限流）
- 把结果变成模型能读的结构化文本
- 再接到 Agent 的 tool 调用里

每一步都有现成工具，但**凑在一起很麻烦**：API key 管理、被墙、格式不统一、文档分散。

CrawlEyes 就是为解决这个痛点做的：**一个分层组合的爬取 + 搜索工具箱，给 AI Agent 一双能「看」和「读」网页的眼睛**。

## CrawlEyes 能做什么

### 1. 抓取：URL → 干净 Markdown

```bash
python scripts/crawl4ai_cli.py https://example.com --text
```

基于 Crawl4AI（无头浏览器），处理 **~80% 的 JS 渲染 / 动态加载 / UA 拦截**页面，输出干净的 Markdown。

四个实用能力：
- **正文降噪**：去掉导航/广告/评论。实测典型文章 **24.6k → 2.8k 字符（~89% 噪声被清除）**
- **失败重试**：指数退避（1s/2s/4s），临时故障自动重试
- **会话复用**：同一进程内复用浏览器上下文，多个 URL 不用冷启动
- **关键词聚焦**（实验）：按 BM25 只保留跟某关键词相关的段落

### 2. 搜索：SearXNG 优先 + Tavily 零配置兜底

```python
from crawl_search_standalone import CrawlSearch
r = CrawlSearch(rerank=True).search('AI agent web scraping')
```

- **主搜索**：自托管 SearXNG（隐私友好，不依赖任何厂商）
- **兜底**：Tavily keyless API（**零配置、零 key**，SearXNG 挂了自动切）
- **熔断器**：三次失败 → 60 秒冷却 → 半开探测，防止雪崩
- **共享缓存**：SQLite + TTL，多个 profile 共用一个缓存

### 3. 语义重排：让相关结果浮上来

搜索结果是关键词匹配，不一定相关。CrawlEyes 用本地 embedding 重排：

- `fastembed` + `BAAI/bge-small-zh-v1.5`（512 维）
- **无 torch 依赖**，模型 ~50MB，缓存后加载 ~0.6s，embedding ~50ms
- 实测：**相关结果 0.817/0.732 浮到顶部，无关的 0.302/0.139 沉底**

### 4. MCP Server：任何 Agent 客户端都能接

```bash
python scripts/mcp_crawl_server.py   # 暴露 search() + extract()
```

标准 MCP server（stdio 传输），暴露 `search(query, limit)` + `extract(url, max_words)` 两个工具。**不依赖 Hermes 内部实现**，任何 MCP 客户端（Claude Desktop、Cursor 等）都能直接复用。

## 设计原则

### 分层组合，每层有兜底
没有单一工具能覆盖所有场景。抓取用 Crawl4AI，搜索用 SearXNG + Tavily，每层都有测试过的 fallback。单点挂了不至于全瘫。

### 开箱即用，国内友好
- Tavily keyless 零配置，**不需要注册、不需要 API key**
- 安装走清华源，Playwright 走 npmmirror，HF 模型走 hf-mirror
- **不依赖 Google 系服务**，境内可直接部署使用

### 诚实记录局限
爬虫常见坑如实记录在 `research_log/`：验证码墙标为「不可解」而非绕过（本项目**不做代理池、指纹轮换、验证码对抗**——尊重 robots.txt 和网站条款，合规底线）。

## 我们从踩坑中学到的

开发过程中最值得分享的，是**搜索链路在中国境内真正可用的经验**：

- 默认 SearXNG 配置启用的几乎全是境外引擎（Google/DDG/Brave），境内**一个都连不上**，中文搜索必挂
- 配了境内引擎后，**百度高频查询会被 CAPTCHA 风控**（连续 10 次就封），必须加 yandex 冗余
- bing 在 JSON API 下**静默返回空**（无错误提示），不能当主力
- 最终方案：**baidu + yandex 双主力**，实测 10/10 连续查询稳定

这些踩坑经验都已沉淀进 README 和开发记录，让后来者少走弯路。

## 项目现状

- **GitHub**：https://github.com/waiky-github/CrawlEyes （MIT 协议）
- **生产验证**：Hermes Agent 的 5 个环境（researcher/coder/ops/creative/main）全部接入，每天真实使用
- **代码**：~8 个 Python 脚本 + 1 个插件 + 1 个 MCP server，独立实现，无复制代码
- **合规**：Credits 里清晰标注了所有灵感来源（Crawl4AI/Readability/Firecrawl 等 9 个项目的授权）

## 欢迎参与

- ⭐ Star 支持
- 🐛 提 Issue：功能建议、bug、文档改进
- 🤝 提 PR：任何方向的贡献

如果你也在做 AI Agent、RAG、或任何需要「让 Agent 看网页」的事，试试 CrawlEyes，也许能省你几天的组装时间。
