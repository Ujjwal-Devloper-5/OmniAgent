"""
Multi-provider web search with automatic fallback.
Provider chain:
  1. DuckDuckGo Instant Answer API (no key, fastest)
  2. DuckDuckGo HTML scraping fallback
  3. Wikipedia search API (no key)
  4. Graceful error
"""
from __future__ import annotations

import asyncio
import html
import re
import urllib.parse

import httpx
from langchain_core.tools import tool
from core.logger import get_logger

log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(10.0)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OmniAgent/2.0; research bot)"
}


async def _ddg_instant(query: str) -> str | None:
    """DuckDuckGo Instant Answer API — fastest, zero config."""
    try:
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            r = await client.get("https://api.duckduckgo.com/", params=params)
            data = r.json()
        
        parts: list[str] = []
        if data.get("AbstractText"):
            parts.append(f"**Summary**: {data['AbstractText']}")
        if data.get("AbstractURL"):
            parts.append(f"**Source**: {data['AbstractURL']}")
        
        # Related topics
        topics = data.get("RelatedTopics", [])[:5]
        if topics:
            parts.append("\n**Related:**")
            for t in topics:
                if isinstance(t, dict) and t.get("Text"):
                    parts.append(f"  • {t['Text'][:120]}")
        
        if parts:
            return "\n".join(parts)
    except Exception as exc:
        log.debug("DDG instant failed: %s", exc)
    return None


async def _ddg_html(query: str) -> str | None:
    """DuckDuckGo HTML search — scrapes result snippets."""
    try:
        params = {"q": query, "kl": "us-en"}
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            r = await client.get("https://html.duckduckgo.com/html/", params=params)
        
        # Extract result snippets using regex (no beautifulsoup dependency)
        # DDG HTML results have class="result__snippet"
        snippets = re.findall(
            r'class="result__snippet"[^>]*>([^<]+(?:<[^/][^>]*>[^<]*</[^>]+>[^<]*)*)',
            r.text,
        )
        titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>([^<]+)', r.text)
        urls = re.findall(r'class="result__url"[^>]*>\s*([^<\s]+)', r.text)
        
        if not snippets:
            return None
        
        results: list[str] = []
        for i, snippet in enumerate(snippets[:5]):
            clean = html.unescape(re.sub(r'<[^>]+>', '', snippet)).strip()
            if clean:
                title = titles[i].strip() if i < len(titles) else ""
                url = urls[i].strip() if i < len(urls) else ""
                result_line = f"**{title}**" if title else f"Result {i+1}"
                if url:
                    result_line += f" ({url})"
                results.append(f"{result_line}\n{clean}")
        
        return "\n\n".join(results) if results else None
    except Exception as exc:
        log.debug("DDG HTML failed: %s", exc)
    return None


async def _wikipedia_search(query: str) -> str | None:
    """Wikipedia search API — authoritative for factual queries."""
    try:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 3,
            "format": "json",
            "utf8": 1,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params=params,
                headers=_HEADERS,
            )
            data = r.json()
        
        results = data.get("query", {}).get("search", [])
        if not results:
            return None
        
        parts: list[str] = ["**Wikipedia Results:**"]
        for item in results[:3]:
            title = item.get("title", "")
            snippet = html.unescape(re.sub(r"<[^>]+>", "", item.get("snippet", "")))
            url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            parts.append(f"  **{title}**: {snippet}... [{url}]")
        
        return "\n".join(parts)
    except Exception as exc:
        log.debug("Wikipedia search failed: %s", exc)
    return None


@tool
async def web_search(query: str) -> str:
    """
    Search the web and return relevant results.
    
    Uses multiple providers with automatic fallback:
    1. DuckDuckGo Instant Answers (fastest, best for factual queries)
    2. DuckDuckGo web search (broader coverage)
    3. Wikipedia (authoritative encyclopedic content)
    
    Args:
        query: Search query string
    
    Returns:
        Formatted search results with titles, snippets, and source URLs.
    
    Examples:
        web_search("Python asyncio tutorial")
        web_search("latest news about SpaceX")
        web_search("capital of France")
    """
    if not query.strip():
        return "❌ Empty search query."
    
    log.info("web_search: query=%s", query[:80])
    
    # Try providers in order
    for provider_fn, provider_name in [
        (_ddg_instant, "DuckDuckGo Instant"),
        (_ddg_html, "DuckDuckGo Web"),
        (_wikipedia_search, "Wikipedia"),
    ]:
        try:
            result = await provider_fn(query)
            if result and len(result.strip()) > 20:
                log.info("web_search: success via %s", provider_name)
                return f"🔍 **Search Results** (via {provider_name}):\n\n{result}"
        except Exception as exc:
            log.warning("web_search provider %s failed: %s", provider_name, exc)
            continue
    
    return (
        f"❌ Web search returned no results for '{query}'.\n"
        "Try a different query or use `fetch_url` for a specific page."
    )
