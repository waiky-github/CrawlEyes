"""Prompt-injection defense helpers (pure functions, no heavy deps).

`sanitize_markdown` strips invisible characters and prompt-hijack attempts from
extracted page content before it reaches an LLM / agent. Pure stdlib — no MCP
dependency, so it can be imported by the core search/extract/RAG stack without
pulling in the MCP framework.
"""

import re

# ---- Prompt-injection defense (inspired by Scrapling's approach) ----
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|messages?)",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+",
    r"you\s+are\s+now\s+",
    r"system\s*:\s*(you\s+are|ignore)",
    r"<\|?im_start\|?>",
    r"</?(system|assistant|user|human)>",
    r"do\s+not\s+reveal\s+",
    r"new\s+instructions?:",
]


def sanitize_markdown(md: str, max_words: int = 8000) -> str:
    """Strip invisible prompt-injection text and mark suspicious instructions.

    Strategy (similar to Scrapling's AI-facing sanitization):
    1. Remove zero-width / invisible Unicode characters (common injection carriers)
    2. Drop lines that try to hijack the model (case-insensitive, English-centric)
    3. Collapse excessive blank lines
    4. Word-count guardrail against prompt bombs
    """
    if not md:
        return md

    # 1. Remove invisible / zero-width characters (ZWJ, ZWNJ, BOM, word-joiner, etc.)
    md = re.sub(
        r"[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\ufeff\u00ad\u061c]",
        "",
        md,
    )

    # 2. Drop lines that look like prompt-hijack attempts
    out_lines = []
    for line in md.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if re.search(_INJECTION_PATTERNS[0], stripped, re.IGNORECASE) or re.search(
            _INJECTION_PATTERNS[1], stripped, re.IGNORECASE
        ):
            continue  # drop the line entirely
        out_lines.append(line)

    md = "\n".join(out_lines)

    # 3. Collapse 3+ consecutive blank lines to one
    md = re.sub(r"\n{3,}", "\n\n", md)

    # 4. Word-count guardrail
    words = re.findall(r"\S+", md)
    if len(words) > max_words:
        md = " ".join(words[:max_words]) + " ... [truncated]"

    return md
