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

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918 private
    ipaddress.ip_network("172.16.0.0/12"),     # RFC1918 private
    ipaddress.ip_network("192.168.0.0/16"),    # RFC1918 private
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),     # Shared address space
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]

def _check_ssrf(url: str) -> None:
    """Raise ValueError if the URL resolves to a private/internal IP address."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: no hostname")
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for '{hostname}': {e}")
    for result in results:
        ip_str = result[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(
                    f"SSRF blocked: '{hostname}' resolves to '{ip}' which is in reserved network {net}. "
                    f"Only public internet URLs are allowed."
                )


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
        _check_ssrf(url)
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
        try:
            import trafilatura
            content = trafilatura.extract(resp.text, include_tables=True, include_links=False, no_fallback=False)
            if not content:
                # Fallback: basic tag stripping
                import re
                content = re.sub(r'<style[^>]*>.*?</style>', ' ', resp.text, flags=re.DOTALL|re.IGNORECASE)
                content = re.sub(r'<script[^>]*>.*?</script>', ' ', content, flags=re.DOTALL|re.IGNORECASE)
                content = re.sub(r'<[^>]+>', ' ', content)
                content = re.sub(r'\s+', ' ', content).strip()
        except ImportError:
            import re
            content = re.sub(r'<[^>]+>', ' ', resp.text)
            content = re.sub(r'\s+', ' ', content).strip()
        clean = content

        if len(clean) > _MAX_CHARS:
            clean = clean[:_MAX_CHARS] + f"\n\n[... content truncated at {_MAX_CHARS} chars]"

        return f"Content from {url}:\n\n{clean}" if clean else "Page appears to be empty."
    except Exception as exc:
        log.error("URL fetch error for '%s': %s", url, exc)
        return f"Could not fetch URL: {exc}"
