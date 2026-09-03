"""
Central tool registry — eagerly loads all tools at import time.

Tools are loaded in two tiers:
  CORE    : Always available (web search, calculator, datetime, etc.)
  SANDBOX : Docker-based execution — loaded eagerly, fails gracefully if no Docker.

The AI agent receives the full tool list. The system prompt teaches it which
tool to pick for which situation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.calculator import calculate
from tools.code_tool import execute_python
from tools.datetime_tool import get_current_datetime
from tools.search import web_search
from tools.url_tool import fetch_url
from tools.weather_tool import get_weather
from tools.wikipedia_tool import wikipedia_lookup

log = logging.getLogger(__name__)

# New v4 tools — file system and persistent memory
try:
    from tools.file_tool import read_file, write_file, list_files
    _FILE_TOOLS: list[Any] = [read_file, write_file, list_files]
except Exception as _fe:
    log.warning("File tools could not be loaded: %s", _fe)
    _FILE_TOOLS = []

try:
    from tools.memory_tool import remember_note, recall_notes
    _MEMORY_TOOLS: list[Any] = [remember_note, recall_notes]
except Exception as _me:
    log.warning("Memory tools could not be loaded: %s", _me)
    _MEMORY_TOOLS = []

# ── MCP tools — discovered dynamically at startup from mcp_manager ────────────
# (populated after initialize_mcp() is called in main.py startup)
# We don't pre-load them here; get_tools() fetches them live from the manager.

# ── Core tools (always available) ─────────────────────────────────────────────
_CORE_TOOLS: list[Any] = [
    web_search,
    calculate,
    get_current_datetime,
    wikipedia_lookup,
    execute_python,
    get_weather,
    fetch_url,
    *_FILE_TOOLS,
    *_MEMORY_TOOLS,
]

# ── Sandbox tools — attempt to load at import time ────────────────────────────
_SANDBOX_TOOLS: list[Any] = []
_SANDBOX_AVAILABLE: bool = False

try:
    from tools.sandbox_tool import SANDBOX_TOOLS as _ST
    _SANDBOX_TOOLS = list(_ST)
    _SANDBOX_AVAILABLE = True
    log.info("Sandbox tools loaded: %d tools", len(_SANDBOX_TOOLS))
except Exception as _e:
    log.warning("Sandbox tools could not be loaded: %s", _e)
    _SANDBOX_TOOLS = []
    _SANDBOX_AVAILABLE = False

# Upload tools — file delivery to Discord/Telegram/Slack
try:
    from tools.upload_tool import generate_and_upload_pdf, upload_text_file
    _CORE_TOOLS.extend([generate_and_upload_pdf, upload_text_file])
    log.info("Upload tools loaded: generate_and_upload_pdf, upload_text_file")
except Exception as _upload_err:
    log.warning("Upload tools unavailable: %s", _upload_err)


# ── Combined tool list — what the agent sees ──────────────────────────────────
_ALL_TOOLS: list[Any] = _CORE_TOOLS + _SANDBOX_TOOLS


def get_tools() -> list[Any]:
    """
    Return ALL registered tools for the AI agent.
    Always includes core + sandbox tools. Also merges any MCP tools
    discovered at startup by the MCPManager (zero cost if no MCP configured).
    """
    try:
        from tools.mcp_manager import get_mcp_tools
        mcp = get_mcp_tools()
        if mcp:
            return list(_ALL_TOOLS) + mcp
    except Exception:
        pass
    return list(_ALL_TOOLS)



def get_core_tools() -> list[Any]:
    """Return only core tools (no sandbox)."""
    return list(_CORE_TOOLS)


def get_sandbox_tools() -> list[Any]:
    """Return only sandbox tools (empty list if Docker not available)."""
    return list(_SANDBOX_TOOLS)


def is_sandbox_available() -> bool:
    """True if sandbox Docker tools were successfully imported."""
    return _SANDBOX_AVAILABLE


def get_tool_summary() -> str:
    """
    Return a human-readable summary of all registered tools.
    Used for logging and debugging.
    """
    tool_names = [t.name if hasattr(t, "name") else str(t) for t in _ALL_TOOLS]
    sandbox_names = [t.name if hasattr(t, "name") else str(t) for t in _SANDBOX_TOOLS]
    return (
        f"Registered tools ({len(_ALL_TOOLS)} total): {', '.join(tool_names)}\n"
        f"Sandbox tools ({'available' if _SANDBOX_AVAILABLE else 'unavailable'}): "
        f"{', '.join(sandbox_names) or 'none'}"
    )
