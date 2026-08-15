"""
Background health monitor that periodically pings all configured AI providers
and updates their health status in the ModelRouter.

Runs as an asyncio background task started in main.py.
"""

from __future__ import annotations

import asyncio

from core.logger import get_logger
from core.model_router import get_router

log = get_logger(__name__)


async def run_health_monitor() -> None:
    """
    Continuously run health checks on all configured AI providers.
    Updates the router's health state so routing decisions are always fresh.
    """
    from config import settings

    interval = settings.health_check_interval_seconds
    log.info("Health monitor started | interval=%ds", interval)

    while True:
        try:
            await asyncio.sleep(interval)
            await _run_single_check()
        except asyncio.CancelledError:
            log.info("Health monitor cancelled")
            break
        except Exception as exc:
            log.error("Health monitor unexpected error: %s", exc, exc_info=True)
            await asyncio.sleep(30)  # Short retry on unexpected errors


async def _run_single_check() -> None:
    """Run one round of health checks across all providers."""
    router = get_router()
    log.debug("Running health check round...")

    for provider, agent in router._agents.items():
        try:
            healthy = await agent.health_check()
            current = router._health[provider]
            was_healthy = current.is_healthy

            if healthy:
                router._record_success(provider)
                if not was_healthy:
                    log.info("Provider %s is back ONLINE", provider.value)
            else:
                router._record_failure(provider)
                if was_healthy:
                    log.warning("Provider %s went OFFLINE", provider.value)
        except Exception as exc:
            log.warning("Health check error for %s: %s", provider.value, exc)
            router._record_failure(provider)

    # Log summary
    report = await router.get_health_report_async()
    healthy_providers = [p for p, info in report.items() if info["healthy"]]
    log.info(
        "Health check complete | healthy=%s",
        healthy_providers if healthy_providers else "NONE",
    )
