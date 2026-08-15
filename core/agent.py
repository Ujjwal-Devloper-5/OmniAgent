"""
Public agent API — single interface for all platform adapters.
Delegates to ModelRouter which handles provider selection, health, and fallback.
"""

from __future__ import annotations

from core.agents.base import AgentResponse, ModelProvider, TaskType
from core.logger import get_logger
from core.model_router import classify_task, get_router

log = get_logger(__name__)


async def process_message(
    session_id:     str,
    message:        str,
    platform:       str         = "unknown",
    force_provider: str | None  = None,
) -> str:
    """
    Process a user message using the best available AI provider.

    The router will:
    1. Classify the task (coding, math, creative, research, etc.)
    2. Select the best configured + healthy provider for that task
    3. Fall back through the full chain if preferred provider fails
    4. Raise RuntimeError only if ALL providers fail

    Parameters
    ----------
    session_id     : Unique conversation thread ID.
    message        : The user's message text.
    platform       : Platform name for logging.
    force_provider : Force a specific provider ("gemini", "openai",
                     "anthropic", "groq", "openrouter", "ollama").

    Returns
    -------
    str — AI response text, with fallback notice appended if applicable.
    """
    router = get_router()

    fp: ModelProvider | None = None
    if force_provider:
        try:
            fp = ModelProvider(force_provider.lower())
        except ValueError:
            log.warning("Unknown provider '%s', using auto-routing", force_provider)

    response: AgentResponse = await router.route(
        session_id=session_id,
        message=message,
        platform=platform,
        force_provider=fp,
    )

    content = response.content

    # Append small fallback notice if another provider was used
    if response.fallback_used and response.fallback_from:
        notice = (
            f"\n\n> ⚠️ _{response.fallback_from.value.capitalize()} was unavailable. "
            f"Response from **{response.provider.value.capitalize()}** "
            f"({response.model_name})._"
        )
        content = content + notice

    return content


async def clear_memory(session_id: str) -> None:
    """Clear conversation history across ALL providers for this session."""
    log.info("Clearing all provider memories | session=%s", session_id)
    await get_router().clear_all_memory(session_id)
    log.info("All memories cleared | session=%s", session_id)


async def get_status() -> dict:
    """Return live health status of all AI providers."""
    return await get_router().get_health_report_async()


async def get_free_providers() -> list[str]:
    """Return list of configured free/budget providers."""
    return await get_router().get_free_providers()


def get_task_classification(message: str) -> str:
    """Return human-readable task classification for a message."""
    return classify_task(message).value
