"""
Web search tool using DuckDuckGo (no API key required).
Uses the ddgs package directly for maximum compatibility.
"""

from __future__ import annotations

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)


@tool
def web_search(query: str) -> str:
    """
    Search the internet for current information, news, facts, tutorials,
    or anything else. Use this when the user asks about recent events,
    wants up-to-date facts, or anything that might not be in training data.

    Args:
        query: The search query string.

    Returns:
        Summarised search results as a string.
    """
    log.debug("Web search: %s", query)
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))

        if not results:
            return "No results found for that query. Try rephrasing."

        # Format results into readable text
        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            body = r.get("body", "No description")
            href = r.get("href", "")
            formatted.append(f"{i}. **{title}**\n   {body}\n   {href}")

        return "\n\n".join(formatted)

    except Exception as exc:
        log.error("Web search failed: %s", exc)
        return f"Search failed: {exc}. Please try again."
