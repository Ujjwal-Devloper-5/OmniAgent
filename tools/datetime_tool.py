"""
Datetime and timezone tools.
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)


@tool
def get_current_datetime(timezone_name: str = "UTC") -> str:
    """
    Get the current date and time, optionally in a specific timezone.
    Common timezones: UTC, US/Eastern, US/Pacific, Europe/London,
    Asia/Kolkata, Asia/Tokyo, Australia/Sydney.

    Args:
        timezone_name: IANA timezone name (default: UTC).

    Returns:
        Current date and time as a formatted string.
    """
    log.debug("Datetime tool called with tz=%s", timezone_name)
    try:
        import zoneinfo

        if timezone_name.upper() == "UTC":
            tz = timezone.utc
        else:
            tz = zoneinfo.ZoneInfo(timezone_name)

        now = datetime.now(tz=tz)
        return (
            f"Current date/time in {timezone_name}: "
            f"{now.strftime('%A, %B %d, %Y at %I:%M:%S %p %Z')}"
        )
    except Exception as exc:
        log.error("Datetime tool error: %s", exc)
        # Fallback
        now = datetime.now(timezone.utc)
        return f"UTC: {now.strftime('%A, %B %d, %Y at %I:%M:%S %p UTC')} (timezone error: {exc})"
