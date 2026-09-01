"""Deep research agent for CrawlEyes.

Turns a topic into a cited research report using CrawlEyes' own search + extraction
stack (SearXNG → Tavily keyless + Crawl4AI extraction + denoising + sanitization).

Pipeline (mirrors gpt-researcher / OpenDeepResearch architecture):
    1. LLM decomposes the topic into sub-questions (query planning)
    2. Each sub-question → search (SearXNG → Tavily keyless fallback)
    3. Top results → extraction + denoising + prompt-injection stripping
    4. LLM synthesizes a cited Markdown report

Zero external accounts: uses Tavily keyless by default. The LLM layer is optional —
configure any OpenAI-compatible endpoint via env vars. Without an LLM, it degrades
to an "aggregate" mode that returns the raw evidence bundle.

Env vars (all optional):
    CRAWLEYES_LLM_BASE_URL   OpenAI-compatible base URL (default: https://api.openai.com/v1)
    CRAWLEYES_LLM_API_KEY    API key (default: empty → aggregate mode)
    CRAWLEYES_LLM_MODEL      model name (default: gpt-4o-mini)
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .crawl_search_standalone import CrawlSearch
from .rag import markdown
from .rate_limit import default_limiter

_search = CrawlSearch(rerank=True)

PLAN_PROMPT = """You are a research planner. Break the following research topic into
{num} focused sub-questions. Each sub-question should be independently searchable and
together they should cover the topic. Return ONLY a JSON array of strings, no prose:

Topic: {topic}
"""

SYNTH_PROMPT = """You are a research analyst. Synthesize the following evidence into a
concise, factual, well-structured Markdown report with clear sections and inline
citations in the form [source N](url).

Topic: {topic}

Evidence:
{evidence}

Rules:
- Base every claim on the evidence; if the evidence is insufficient, say so.
- Cite sources inline with [source N](url).
- End with a "Sources" section listing every cited URL.
- Keep it under {max_words} words.
"""


@dataclass
class ResearchResult:
    topic: str
    sub_questions: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""
    mode: str = "aggregate"  # "aggregate" | "llm"
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "mode": self.mode,
            "sub_questions": self.sub_questions,
            "sources": self.sources,
            "report": self.report,
            "errors": self.errors,
        }


class _LLM:
    """Minimal OpenAI-compatible chat client (no SDK dependency beyond httpx)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        try:
            import httpx
        except ImportError:
            return ""  # LLM 不可用 → 上层降级为 aggregate 模式
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]


def _get_llm() -> _LLM | None:
    base = os.environ.get("CRAWLEYES_LLM_BASE_URL", "https://api.openai.com/v1")
    key = os.environ.get("CRAWLEYES_LLM_API_KEY", "")
    model = os.environ.get("CRAWLEYES_LLM_MODEL", "gpt-4o-mini")
    if not key:
        return None
    return _LLM(base, key, model)


async def _plan(llm: _LLM, topic: str, num: int = 4) -> list[str]:
    try:
        out = await llm.chat(
            system="You return only valid JSON.",
            user=PLAN_PROMPT.format(num=num, topic=topic),
        )
        # tolerate markdown fences around the JSON
        out = out.strip()
        if out.startswith("```"):
            out = out.split("```")[1]
            out = out.removeprefix("json")
        data = json.loads(out)
        if isinstance(data, list):
            return [str(x) for x in data][:num]
        if isinstance(data, dict) and "questions" in data:
            return [str(x) for x in data["questions"]][:num]
    except Exception:
        return []
    return []


async def _gather_evidence(sub_questions: list[str], per_q: int = 3) -> list[dict[str, Any]]:
    """Search each sub-question, then extract the top results."""
    sources: list[dict[str, Any]] = []
    seen: set = set()

    # Phase 1: search all sub-questions (with unified rate limit, P2-H)
    search_batches: list[dict[str, Any]] = []
    for q in sub_questions:
        # deep_research 内部多轮搜索也走统一限流，防打爆下游
        await asyncio.to_thread(default_limiter.acquire, "search", 1)
        r = _search.search(q, limit=per_q * 2)
        if not r.get("success"):
            continue
        search_batches.append({"question": q, "results": r.get("data", {}).get("web", [])})

    # Phase 2: extract top results (dedupe by URL)
    for batch in search_batches:
        for item in batch["results"][:per_q]:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({
                "question": batch["question"],
                "title": item.get("title", ""),
                "url": url,
                "description": item.get("description", ""),
                "content": "",
            })

    # Phase 3: fetch content for top sources (bounded, with unified rate limit P2-H)
    for s in sources[: per_q * len(sub_questions)]:
        try:
            # deep_research 内部抓取也走统一限流（与 MCP extract 同 cost）
            await asyncio.to_thread(default_limiter.acquire, "extract", 3)
            r = await markdown(s["url"], max_words=1500, retry=1, timeout=25)
            if r.get("success"):
                s["content"] = r.get("markdown", "")[:2000]
        except Exception:
            s["content"] = ""

    return sources


def _build_evidence_block(sources: list[dict[str, Any]]) -> str:
    lines = []
    for i, s in enumerate(sources, 1):
        lines.append(f"[source {i}] {s['title']}\nURL: {s['url']}")
        content = (s.get("content") or "").strip()
        if content:
            lines.append(content[:1500])
        elif s.get("description"):
            lines.append((s.get("description") or "")[:500])
        lines.append("")
    return "\n".join(lines)


async def deep_research(topic: str, num_questions: int = 4, per_q: int = 3) -> ResearchResult:
    """Run the full research pipeline and return a ResearchResult."""
    result = ResearchResult(topic=topic)
    llm = _get_llm()

    # 1. Plan sub-questions
    if llm:
        result.sub_questions = await _plan(llm, topic, num_questions)
    if not result.sub_questions:
        # Fallback: single broad query (works even without an LLM)
        result.sub_questions = [topic]

    # 2-3. Gather evidence
    result.sources = await _gather_evidence(result.sub_questions, per_q)
    if not result.sources:
        result.errors.append("No sources found for any sub-question.")
        # Still produce a well-formed report so callers/tests get structured output
        result.mode = "aggregate"
        result.report = (
            f"# Research: {topic}\n\n"
            "_No sources could be retrieved (search unavailable). "
            "Check SEARXNG_URL / network / httpx availability._"
        )
        return result

    # 4. Synthesize
    evidence = _build_evidence_block(result.sources)
    if llm:
        try:
            report = await llm.chat(
                system="You are a meticulous research analyst.",
                user=SYNTH_PROMPT.format(topic=topic, evidence=evidence, max_words=1500),
            )
            result.report = report.strip()
            result.mode = "llm"
            return result
        except Exception as e:
            result.errors.append(f"LLM synthesis failed: {e}")

    # Aggregate mode fallback: build a structured evidence summary ourselves
    result.mode = "aggregate"
    lines = [f"# Research: {topic}", ""]
    for i, s in enumerate(result.sources, 1):
        lines.append(f"## [{i}] {s['title']}")
        lines.append(f"Source: {s['url']}")
        content = (s.get("content") or s.get("description") or "").strip()
        if content:
            lines.append("")
            lines.append(content[:800])
        lines.append("")
    result.report = "\n".join(lines)
    return result


def deep_research_sync(topic: str, **kwargs) -> dict[str, Any]:
    """Synchronous wrapper around :func:`deep_research`."""
    return asyncio.run(deep_research(topic, **kwargs)).to_dict()


__all__ = ["ResearchResult", "deep_research", "deep_research_sync"]
