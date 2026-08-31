# 实测记录：Crawl4AI 落地验证（P0）

> 结论：**Crawl4AI 本机跑通，能破 L1(UA/JS/动态渲染)类反爬；遇 L3/L4(验证码/身份认证)类仍卡**

---

## 一、安装过程（踩的坑，未来可复用）

1. **pip 装包**：`pip install crawl4ai` 默认 pypi.org 极慢(~20KB/s) → **换清华源**秒装
   `.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple crawl4ai`
   → Crawl4AI 0.9.2 安装成功
2. **浏览器内核是最大障碍**：
   - 默认 `cdn.playwright.dev` 在国内 **0 B/s 卡死**（下载 184MB 到 10% 停）
   - 解法：镜像 `PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright`
   - `python -m playwright install chromium` → **114MB 秒下**（npmmirror 200）
   - Chrome Headless Shell 151 / ffmpeg 下载到 `~/.cache/ms-playwright`(656M)

## 二、实测结果

| 目标 | 结果 | 说明 |
|:--|:--|:--|
| example.com | ✅ 1.35s 干净markdown | 基础链路通 |
| **JS/动态渲染文章页** | ✅ 全文可抓 | 破 L1(UA/JS/动态渲染)，痛点头号解决 |
| **风控严格的搜索聚合页** | ❌ 触发验证码墙 | L3/L4：IP 风控需点选验证码，Crawl4AI 无法自动过 |

## 三、结论

- ✅ **Crawl4AI 真能解决"普通网页/文档/动态页抓取受限"**——JS/动态渲染全文可拿下
- ❌ **风控严格的站点仍是难点**——触发验证码，属身份/CAPTCHA 类，Crawl4AI 不动
- 💭 整体判断：Crawl4AI 覆盖**80% 的 L1 抓取受限**（UA/JS/渲染/被墙文档）；**L3/L4(验证码/登录/高频封IP)需其他手段**：等风控降温、带 cookie、或挂代理池

## 四、可复用命令

```bash
# 装(国内): 清华源 + npmmirror浏览器镜像
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple crawl4ai
PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright \
  .venv/bin/python -m playwright install chromium
.venv/bin/crawl4ai-setup

# 抓取示例
.venv/bin/python scripts/crawl4ai_cli.py https://example.com
```

## 信源
- Crawl4AI 0.9.2 本机实测
- 风控站点触发 VerifyCode 图片点选验证码（IP 风控）
