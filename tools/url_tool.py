"""
URL content fetcher and summarizer tool.
Fetches a web page and returns cleaned text content.
"""

from __future__ import annotations

import re

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

_MAX_CHARS = 4000  # Avoid flooding context


@tool
def fetch_url(url: str) -> str:
    """
    Fetch and read the text content of a web page URL.
    Useful when the user shares a URL and wants you to read, summarise,
    or analyse its content. Returns the cleaned text of the page.

    Args:
        url: The full URL to fetch (must start with http:// or https://).

    Returns:
        Cleaned page text content, or an error message.
    """
    log.debug("Fetching URL: %s", url)
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"

    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; OmniAgent/1.0; +https://github.com/omniagent)"
            )
        }
        with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text" not in content_type and "json" not in content_type:
                return f"URL returned non-text content ({content_type}), cannot read."

            text = resp.text

        # Strip HTML tags
        clean = re.sub(r"<[^>]+>", " ", text)
        # Collapse whitespace
        clean = re.sub(r"\s+", " ", clean).strip()

        if len(clean) > _MAX_CHARS:
            clean = clean[:_MAX_CHARS] + f"\n\n[... content truncated at {_MAX_CHARS} chars]"

        return f"Content from {url}:\n\n{clean}" if clean else "Page appears to be empty."
    except Exception as exc:
        log.error("URL fetch error for '%s': %s", url, exc)
        return f"Could not fetch URL: {exc}"
