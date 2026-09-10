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

# Elite coding tools — Phase 4
try:
    from tools.code_tools import (
        view_file as sandbox_view_file,
        edit_file as sandbox_edit_file,
        grep_search,
        find_files,
        manage_file,
    )
    _CODE_TOOLS: list[Any] = [
        sandbox_view_file,
        sandbox_edit_file,
        grep_search,
        find_files,
        manage_file,
    ]
    log.info("Elite coding tools loaded: %d tools", len(_CODE_TOOLS))
except Exception as _ce:
    log.warning("Elite coding tools could not be loaded: %s", _ce)
    _CODE_TOOLS = []

try:
    from tools.memory_tool import remember_note, recall_notes, forget_note
    _MEMORY_TOOLS: list[Any] = [remember_note, recall_notes, forget_note]
except Exception as _me:
    log.warning("Memory tools could not be loaded: %s", _me)
    _MEMORY_TOOLS = []

# ── MCP tools — discovered dynamically at startup from mcp_manager ────────────
# (populated after initialize_mcp() is called in main.py startup)
# We don't pre-load them here; get_tools() fetches them live from the manager.

# ── Core tools (always available) ─────────────────────────────────────────────
log.warning("execute_python disabled — all code execution routes to run_sandbox_command (Docker sandbox)")
_CORE_TOOLS: list[Any] = [
    web_search,
    calculate,
    get_current_datetime,
    wikipedia_lookup,
    get_weather,
    fetch_url,
    *_FILE_TOOLS,
    *_CODE_TOOLS,
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
    from tools.upload_tool import upload_file, deliver_sandbox_file
    _CORE_TOOLS.extend([upload_file, deliver_sandbox_file])
    log.info("Universal file delivery tools loaded: upload_file, deliver_sandbox_file")
except Exception as _upload_err:
    log.warning("Upload tools unavailable: %s", _upload_err)

# Document extraction — Phase 4
try:
    from tools.document_tool import extract_document
    _DOCUMENT_TOOLS: list[Any] = [extract_document]
    log.info("Document extraction tool loaded")
except Exception as _de:
    log.warning("Document tool could not be loaded: %s", _de)
    _DOCUMENT_TOOLS = []

# System status — Phase 4
try:
    from tools.system_tool import system_status
    _SYSTEM_TOOLS: list[Any] = [system_status]
    log.info("System status tool loaded")
except Exception as _se:
    log.warning("System tool could not be loaded: %s", _se)
    _SYSTEM_TOOLS = []

_CORE_TOOLS.extend(_DOCUMENT_TOOLS + _SYSTEM_TOOLS)

# Process management tools — Phase 4
try:
    from tools.process_tool import spawn_background_process, manage_process
    _PROCESS_TOOLS: list[Any] = [spawn_background_process, manage_process]
    log.info("Process management tools loaded")
except Exception as _pe:
    log.warning("Process tools could not be loaded: %s", _pe)
    _PROCESS_TOOLS = []

# Git tool — Phase 4
try:
    from tools.git_tool import git_run
    _GIT_TOOLS: list[Any] = [git_run]
    log.info("Git tool loaded")
except Exception as _ge:
    log.warning("Git tool could not be loaded: %s", _ge)
    _GIT_TOOLS = []

# Browser automation — Phase 4
try:
    from tools.browser_tool import browser_act
    _BROWSER_TOOLS: list[Any] = [browser_act]
    log.info("Browser automation tool loaded")
except Exception as _be:
    log.warning("Browser tool could not be loaded: %s", _be)
    _BROWSER_TOOLS = []

_CORE_TOOLS.extend(_PROCESS_TOOLS + _GIT_TOOLS + _BROWSER_TOOLS)

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

# Tool tiers — based on model capability requirements
# COMPACT models (<7B parameters) hallucinate on complex tool schemas
# They should only receive a safe essential subset
_ESSENTIAL_TOOLS = frozenset({
    "web_search", "calculate", "get_current_datetime", "get_weather",
    "wikipedia_lookup", "remember_note", "recall_notes", "fetch_url",
})

# Tool capability tiers (what model score threshold unlocks what tools)
_TOOL_TIERS: list[tuple[int, set[str]]] = [
    # tool_reliability >= 8: all tools
    (8, set()),  # empty means ALL tools
    # tool_reliability >= 5: no browser, no background processes
    (5, {"browser_act", "spawn_background_process", "manage_process"}),
    # tool_reliability < 5: only essentials
    (0, None),  # None means essential only
]

def get_tools_for_model(
    tool_reliability: int,
    include_mcp: bool = True,
) -> list[Any]:
    """
    Return the appropriate tool set for a model based on its tool_reliability score.
    
    High-reliability models (score 8+) get the full arsenal.
    Medium-reliability models (score 5-7) get core tools without complex browser/process tools.
    Low-reliability models (<5) get only the 8 essential tools to prevent hallucination.
    
    Args:
        tool_reliability: Model's tool_reliability score from ModelRegistry (1-10)
        include_mcp:      Whether to include MCP tools (only for high-reliability models)
    
    Returns:
        Filtered list of tools appropriate for this model.
    """
    all_tools = get_tools()  # full set from registry
    
    if tool_reliability >= 8:
        # Full arsenal
        return all_tools
    elif tool_reliability >= 5:
        # Remove complex tools that small models hallucinate on
        _excluded = {"browser_act", "spawn_background_process", "manage_process"}
        return [
            t for t in all_tools
            if (t.name if hasattr(t, 'name') else getattr(t, '__name__', '')) not in _excluded
        ]
    else:
        # Essential only — prevent hallucination spiral
        return [
            t for t in all_tools
            if (t.name if hasattr(t, 'name') else getattr(t, '__name__', '')) in _ESSENTIAL_TOOLS
        ]
