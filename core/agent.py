"""
Public agent API — single interface for all platform adapters.
Delegates to ModelRouter which handles provider selection, health, and fallback.
"""

from __future__ import annotations

from core.agents.base import AgentResponse, ModelProvider, TaskType
from core.logger import get_logger
from core.model_router import classify_task, get_router
import asyncio

log = get_logger(__name__)

_SWARM_TRIGGERS = frozenset({
    "deep research", "research report", "create a report", "generate a report",
    "generate pdf", "create pdf", "make a pdf", "comprehensive report",
    "detailed research", "in-depth research", "full analysis", "complete analysis",
    "research and write", "investigate and report",
})


async def warm_up_router() -> None:
    """
    Call this at startup to probe all providers in parallel.
    Subsequent route() calls use the cache — zero latency per message.
    """
    router = get_router()
    await router.probe_all_providers()


async def process_message(
    session_id:     str,
    message:        str,
    platform:       str         = "unknown",
    force_provider: str | None  = None,
    has_media:      bool        = False,
    image_data:     bytes | None = None,
    image_mime:     str          = "image/jpeg",
) -> str:
    """
    Process a user message using the best available AI provider.

    The router will:
    1. Classify the task (coding, math, creative, research, vision, etc.)
    2. If has_media=True, immediately route to a vision-capable model
    3. Select the best configured + healthy provider for that task
    4. Fall back through the full chain if preferred provider fails
    5. Raise RuntimeError only if ALL providers fail

    Parameters
    ----------
    session_id     : Unique conversation thread ID.
    message        : The user's message text.
    platform       : Platform name for logging.
    force_provider : Force a specific provider ("gemini", "openai",
                     "anthropic", "groq", "openrouter", "ollama").
    has_media      : True when message includes a file/photo/image.
    image_data     : Raw image bytes (downloaded from Discord/Telegram).
                     When provided, passed directly to vision-capable models
                     (Gemini, OpenAI) — they actually SEE the image.
    image_mime     : MIME type of the image (e.g. "image/png", "image/jpeg").

    Returns
    -------
    str — AI response text, with fallback notice appended if applicable.
    """
    # ── Intercept capability/tool questions ───────────────────────────────────
    # LLMs are often fine-tuned with a hardcoded tool list and will lie about
    # what tools they have regardless of system prompt.  Answer this directly
    # from the registry — the REAL ground truth.
    if _is_tool_capability_question(message):
        return _build_capability_response()

    # ── Swarm detection — complex multi-step tasks ────────────────────────────
    msg_lower = message.lower()
    if any(trigger in msg_lower for trigger in _SWARM_TRIGGERS):
        log.info("Swarm triggered for session=%s", session_id)
        try:
            from core.swarm import run_swarm
            result = await run_swarm(message, session_id, platform)
            # Store in unified memory
            try:
                from core.memory import get_memory
                mem = get_memory()
                asyncio.ensure_future(mem.add_turn(session_id, "user", message))
                asyncio.ensure_future(mem.add_turn(session_id, "assistant", result))
            except Exception:
                pass
            return result
        except Exception as swarm_exc:
            log.warning("Swarm failed, falling back to normal routing: %s", swarm_exc)
            # Fall through to normal routing

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
        has_media=has_media,
        image_data=image_data,
        image_mime=image_mime,
    )

    content = response.content

    # Professional and minimalistic footer
    provider_name = response.provider.value.capitalize()
    model_name = response.model_name
    tokens = response.tokens_used
    
    footer = f"\n\n_— {provider_name} ({model_name}) · {tokens} tokens_"
    content = content + footer

    return content


def _is_tool_capability_question(message: str) -> bool:
    """Detect if the user is asking about capabilities / tool list."""
    msg = message.lower().strip()
    patterns = [
        "what tool", "which tool", "what can you do", "what are your tool",
        "list your tool", "show your tool", "your capabilities", "what capabilities",
        "what do you have", "what features", "do you have sandbox", "can you run",
        "can you execute", "do you have sandbox", "do you have run_sandbox",
        "do you have write_sandbox", "your skills", "what skill",
        "added sandbox", "added tool", "new tool", "updated tool",
        "what are you capable", "capabilities you have",
    ]
    return any(p in msg for p in patterns)


def _build_capability_response() -> str:
    """Build an accurate tool list response directly from the registry."""
    from tools.registry import get_tools, is_sandbox_available

    tools = get_tools()
    sandbox_ok = is_sandbox_available()

    lines = ["Here's my **complete, real-time tool list** — injected directly from the runtime registry:\n"]
    lines.append("| Tool | Description |")
    lines.append("|------|-------------|")

    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
        desc = ""
        if hasattr(t, "description") and t.description:
            desc = t.description.split("\n")[0].strip()[:90]
        elif hasattr(t, "__doc__") and t.__doc__:
            desc = t.__doc__.strip().split("\n")[0][:90]
        lines.append(f"| `{name}` | {desc} |")

    lines.append("")
    if sandbox_ok:
        lines.append("✅ **Sandbox is ACTIVE** — `run_sandbox_command` runs real shell commands in an isolated Docker container with full internet + pip access.")
    else:
        lines.append("⚠️ **Sandbox offline** — `run_sandbox_command` is registered but Docker is not reachable from the container right now.")

    lines.append("\nJust ask me to use any of these — I'll call them automatically when needed.")
    return "\n".join(lines)



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


def get_task_classification(message: str, has_media: bool = False) -> str:
    """Return human-readable task classification for a message."""
    return classify_task(message, has_media=has_media).value
