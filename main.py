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
    from core.model_router import get_router

    # Warm up the router singleton (initialises all agent backends)
    router = get_router()
    health = await router.get_health_report_async()
    configured = [p for p, info in health.items() if info["configured"]]

    log.info("=" * 65)
    log.info("  OmniAgent v2.0.0  —  Multi-Agent Edition")
    log.info("  Configured providers : %s", configured or ["NONE"])
    log.info("  Fallback order       : %s", settings.fallback_order_list)
    log.info("  DB                   : %s", settings.db_path)
    log.info("  Discord              : %s", "enabled" if settings.discord_token else "disabled")
    log.info("  Telegram             : %s", "enabled" if settings.telegram_token else "disabled")
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

    if not tasks:
        log.error("No platform tokens configured! Set DISCORD_TOKEN and/or TELEGRAM_TOKEN.")
        sys.exit(1)

    # Background health monitor
    tasks.append(
        asyncio.create_task(run_health_monitor(), name="health_monitor")
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
