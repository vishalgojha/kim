import html
import os
import re
from urllib.parse import quote_plus

import httpx

from .registry import tool

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36 KimAgent/0.1"
}


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


@tool(
    "web_search",
    "Search the web (DuckDuckGo) and return titles/URLs/snippets. Use for questions about the world, docs, packages, current info.",
    {
        "query": {"type": "string", "description": "search query", "required": True},
        "limit": {"type": "integer", "description": "max results (default 5)", "required": False},
    },
    timeout=30,
)
def web_search(query: str, limit: int = 5) -> str:
    searxng = os.environ.get("SEARXNG_URL", "").strip().rstrip("/")
    if searxng:
        try:
            response = httpx.get(f"{searxng}/search", params={"q": query, "format": "json", "categories": "general"}, headers=_HEADERS, timeout=20)
            data = response.json()
            rows = [f"- {item.get('title', '(untitled)')}\\n  {item.get('url', '')}\\n  {item.get('content', '')}" for item in data.get("results", [])[:max(1, min(int(limit), 20))]]
            return "\\n".join(rows) if rows else "no results"
        except Exception as e:
            return f"private search failed: {e}"
    try:
        r = httpx.get(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            headers=_HEADERS,
            timeout=20,
            follow_redirects=True,
        )
    except Exception as e:
        return f"search failed: {e}"
    page = r.text
    results: list[str] = []
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)<a[^>]+class="result__snippet"', page, flags=re.S)
    if not blocks:
        return "no results (DDG may be blocking; try again or rephrase)"
    for href, title, _snippet in blocks[:limit]:
        if href.startswith("//duckduckgo.com/l/?uddg="):
            inner = re.search(r"uddg=([^&]+)", href)
            href = inner.group(1) if inner else href
            import urllib.parse

            href = urllib.parse.unquote(href)
        title = _strip_tags(title)
        results.append(f"- {title}\n  {href}")
    return "\n".join(results) if results else "no results"


@tool(
    "web_fetch",
    "Fetch a URL and return readable text content (bounded). Use to read a page, docs, or API output.",
    {"url": {"type": "string", "description": "http(s) URL", "required": True}, "max_chars": {"type": "integer", "description": "max chars (default 8000)", "required": False}},
    timeout=40,
)
def web_fetch(url: str, max_chars: int = 8000) -> str:
    if not url.startswith(("http://", "https://")):
        return "only http(s) urls are supported"
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    except Exception as e:
        return f"fetch failed: {e}"
    ctype = r.headers.get("content-type", "")
    if "text" not in ctype and "json" not in ctype and "xml" not in ctype and "html" not in ctype:
        return f"fetched {len(r.content)} bytes of {ctype}; not returning binary"
    text = r.text[: max_chars * 2]
    if "html" in ctype:
        text = _strip_tags(text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return f"HTTP {r.status_code} {r.url}\n" + text
