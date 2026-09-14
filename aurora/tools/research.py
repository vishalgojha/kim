"""Cited, bounded research workflow for Kim."""

from __future__ import annotations

import re

from .registry import tool
from .web import web_fetch, web_search


@tool(
    "deep_research",
    "Run a bounded multi-source research pass. Search, fetch several relevant pages, and return numbered citations plus evidence for Kim to synthesize.",
    {"question": {"type": "string", "description": "research question", "required": True}, "depth": {"type": "integer", "description": "1-3, default 2", "required": False}, "max_sources": {"type": "integer", "description": "maximum pages to fetch, default 5", "required": False}},
    timeout=110,
)
def deep_research(question: str, depth: int = 2, max_sources: int = 5) -> str:
    question = question.strip()
    if not question:
        return "research question is required"
    limit = max(2, min(8, int(max_sources) + max(0, min(2, int(depth) - 2))))
    results = web_search(question, limit=limit * 2)
    urls = re.findall(r"https?://[^\s)]+", results)
    seen: set[str] = set()
    reports = []
    for url in urls:
        url = url.rstrip(".,>")
        if url in seen:
            continue
        seen.add(url)
        page = web_fetch(url, max_chars=9000)
        if page.startswith("fetch failed"):
            continue
        reports.append(f"[{len(reports)+1}] {url}\n{page}")
        if len(reports) >= limit:
            break
    if not reports:
        return "research found no fetchable sources; search result: " + results[:4000]
    return "Research evidence (cite these source numbers in the final answer):\n\n" + "\n\n".join(reports)
