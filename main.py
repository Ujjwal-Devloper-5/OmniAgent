"""
Production entry point with:
- Structured logging initialisation
- Graceful shutdown via signal handling
- Concurrent Discord + Telegram runners
- Background health monitor for all AI providers
"""

from __future__ import annotations

import asyncio
import signal
import sys

from core.logger import get_logger, init_logging

# Initialise logging FIRST before anything else
init_logging()

log = get_logger(__name__)


async def _run_bots() -> None:
    """Launch all platform bots + background health monitor concurrently."""
    from adapters.discord_bot import start_discord
    from adapters.telegram_bot import start_telegram
    from config import settings
    from core.health_monitor import run_health_monitor
    from core.agent import warm_up_router

    # ── MCP Server Initialization ─────────────────────────────────────────────
    # Connect to configured MCP servers and load their tools before providers are probed.
    # This ensures MCP tools are part of the agent tool list from the very first request.
    from tools.mcp_manager import initialize_mcp
    log.info("Initializing MCP servers...")
    await initialize_mcp()

    # ── Boot-time parallel provider probe ─────────────────────────────────────
    # Probes ALL providers concurrently (8s timeout each).
    # After this, all route() calls use the cached results — zero latency.
    log.info("Probing all AI providers in parallel...")
    await warm_up_router()

    from core.model_router import get_router
    health = await get_router().get_health_report_async()
    configured = [p for p, info in health.items() if info["configured"]]
    vision_caps = [p for p, info in health.items() if "vision" in info.get("capabilities", [])]

    log.info("=" * 65)
    log.info("  OmniAgent v2.0.0  —  Smart Multi-Agent Edition")
    log.info("  Configured providers : %s", configured or ["NONE"])
    log.info("  Vision-capable       : %s", vision_caps or ["none"])
    log.info("  Fallback order       : %s", settings.fallback_order_list)
    log.info("  DB                   : %s", settings.db_path)
    log.info("  Discord              : %s", "enabled" if settings.discord_token else "disabled")
    log.info("  Telegram             : %s", "enabled" if settings.telegram_token else "disabled")

    # Log every registered tool so it's visible in docker logs
    from tools.registry import get_tool_summary
    for line in get_tool_summary().splitlines():
        log.info("  %s", line)

    log.info("=" * 65)

    if not configured:
        log.error(
            "No AI providers configured! Set at least one of:\n"
            "  GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY\n"
            "  or start Ollama locally."
        )
        sys.exit(1)

    tasks: list[asyncio.Task] = []

    if settings.discord_token:
        tasks.append(asyncio.create_task(start_discord(), name="discord"))
    else:
        log.warning("Discord bot disabled (no DISCORD_TOKEN)")

    if settings.telegram_token:
        tasks.append(asyncio.create_task(start_telegram(), name="telegram"))
    else:
        log.warning("Telegram bot disabled (no TELEGRAM_TOKEN)")

    from adapters.slack_bot import start_slack
    if settings.slack_bot_token and settings.slack_app_token:
        tasks.append(asyncio.create_task(start_slack(), name="slack"))
    else:
        log.warning("Slack bot disabled (no SLACK_BOT_TOKEN / SLACK_APP_TOKEN)")

    if not tasks:
        log.error("No platform tokens configured! Set DISCORD_TOKEN and/or TELEGRAM_TOKEN.")
        sys.exit(1)

    # Background health monitor
    tasks.append(
        asyncio.create_task(run_health_monitor(), name="health_monitor")
    )

    # Background OpenRouter free-model prober (runs 30s after boot, then every 12h)
    from tools.openrouter_prober import run_openrouter_prober_loop
    tasks.append(
        asyncio.create_task(run_openrouter_prober_loop(), name="openrouter_prober")
    )

    log.info("All services started. Press Ctrl+C to stop.")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for task, result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            log.error("Service '%s' exited with error: %s", task.get_name(), result)
        else:
            log.info("Service '%s' finished", task.get_name())


def _setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Register SIGINT / SIGTERM for graceful shutdown."""

    def _shutdown(sig: signal.Signals) -> None:
        log.info("Signal %s received — shutting down gracefully...", sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except NotImplementedError:
            pass  # Windows


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _setup_signal_handlers(loop)

    try:
        loop.run_until_complete(_run_bots())
    except asyncio.CancelledError:
        log.info("All tasks cancelled cleanly.")
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as exc:
        log.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                log.info("Cancelling %d remaining tasks...", len(pending))
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        finally:
            loop.close()
            log.info("OmniAgent shutdown complete.")


if __name__ == "__main__":
    main()
