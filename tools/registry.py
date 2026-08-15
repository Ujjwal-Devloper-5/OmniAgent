"""
Central tool registry. Import all tools here.
"""

from __future__ import annotations

from typing import Any

from tools.calculator import calculate
from tools.code_tool import execute_python
from tools.datetime_tool import get_current_datetime
from tools.search import web_search
from tools.url_tool import fetch_url
from tools.weather_tool import get_weather
from tools.wikipedia_tool import wikipedia_lookup

ALL_TOOLS: list[Any] = [
    web_search,
    calculate,
    get_current_datetime,
    wikipedia_lookup,
    execute_python,
    get_weather,
    fetch_url,
]


def get_tools() -> list[Any]:
    """Return all registered tools for the AI agent."""
    return ALL_TOOLS
