"""
Wikipedia summary lookup tool.
"""

from __future__ import annotations

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)


@tool
def wikipedia_lookup(query: str, sentences: int = 5) -> str:
    """
    Look up a topic on Wikipedia and return a concise summary.
    Use this to get factual background information about people, places,
    events, concepts, organisations, etc.

    Args:
        query: The topic to look up (e.g. "Quantum computing", "Alan Turing").
        sentences: Number of summary sentences to return (default: 5, max: 10).

    Returns:
        Wikipedia summary or an error message.
    """
    log.debug("Wikipedia lookup: %s", query)
    try:
        import wikipedia  # type: ignore[import-untyped]

        sentences = min(int(sentences), 10)
        result = wikipedia.summary(query, sentences=sentences, auto_suggest=True)
        page = wikipedia.page(query, auto_suggest=True)
        return f"**{page.title}**\n\n{result}\n\nSource: {page.url}"
    except Exception as exc:
        log.warning("Wikipedia lookup failed for '%s': %s", query, exc)
        return f"Could not find Wikipedia article for '{query}': {exc}"
