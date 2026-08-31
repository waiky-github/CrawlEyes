# P3 评估：Firecrawl 云外援（整站/截图场景）

> 日期：2026-08-31 ｜ 状态：**评估完成，结论=低优先暂不接**
> 定位：Crawl4AI 主引擎够不着的"整站归档/高保真截图/云渲染"场景的云外援

---

## 一、最新定价与信用规则（2026-08 核实）

| 档位 | 额度 | 月费 | 说明 |
|:--|:--|:--|:--|
| Free | 1000 credits/月 | $0 | 无信用卡，1 次性发放（部分渠道显示 500） |
| Hobby | 5000 credits | ~$19/月 | 付费起点 |
| Standard | 100k credits | ~$83/月 | 中小量 |

**信用消耗**（官方 firecrawl.dev/pricing）：
- Scrape / Crawl / Map / Monitor = **1 credit/页**
- Search = 2 credits / 10 结果
- Interact（浏览器操作）= 2 credits / 浏览器分钟
- Research Index paper 端点免费

## 二、本机可行性判断

| 维度 | 结论 |
|:--|:--|
| 免费额度 | 1000 credits ≈ 1000 页单页抓取；整站 Crawl 一次就烧几十~几百页 |
| 本机资源 | 低配小内存机 → 自托管 Docker 方案内存不够 |
| 云 API | 数据出境 + 依赖境外直连（Tavily 直连可用，但 Firecrawl 未实测） |
| 与现状重叠 | Crawl4AI 已解决 80% 单页抓取；Firecrawl 独有的是整站/截图/云渲染 |

## 三、结论

**暂不接入，保持"按需外援"姿态**：
- ✅ 已覆盖：单页/文档/动态页抓取（Crawl4AI 本地，免 key）
- 🔶 Firecrawl 独有：**整站转 RAG / 页面截图 / 云渲染过反爬**
- 🚦 触发条件：出现"必须整站归档 或 高保真截图"且**单次小批量（<500 页）**的强需求时，接免费档（1000 credits）做一次性外援即可，不值得长期订阅
- ⚠️ 合规：抓取遵守 robots.txt；敏感数据不送境外

## 信源
- firecrawl.dev/pricing（2026-08-31 抓取）
- Firecrawl pricing 第三方评测（filipkonecny.com / puzzleinbox / proxyhorizon 等）
- GitHub API firecrawl 元数据（AGPL-3.0, 173k star, 2026-08-28 活跃）
