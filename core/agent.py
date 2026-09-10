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



async def warm_up_router() -> None:
    """
    Call this at startup to probe all providers in parallel.
    Subsequent route() calls use the cache — zero latency per message.
    """
    router = get_router()
    await router.probe_all_providers()


async def process_message(
    session_id:               str,
    message:                  str,
    platform:                 str         = "unknown",
    force_provider:           str | None  = None,
    has_media:                bool        = False,
    image_data:               bytes | None = None,
    image_mime:               str          = "image/jpeg",
    raw_message:              str | None  = None,
    routing_policy_override:  str | None  = None,
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
    raw_message    : The original unprocessed user prompt.
    routing_policy_override : Temporarily override the global routing policy for
                     this call only ("SPEED", "QUALITY", "AUTO", "ECO", "OFFLINE").
                     Used by swarm sub-agents to select role-appropriate models
                     (e.g. ResearchAgent → SPEED, QA Reviewer → QUALITY).
                     NOTE: Not thread-safe across concurrent calls with different
                     overrides; safe for our single-process async event loop.
                     TODO: Replace with per-call policy kwarg in router.route()
                     in a future refactor to eliminate the global-state mutation.

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

    # ── Professional task classification ──────────────────────────────────────
    from core.task_classifier import classify as _classify_task
    
    # Classify the RAW user prompt so injected context/history doesn't trigger false positives
    classifier_input = raw_message if raw_message else message
    _decision = _classify_task(classifier_input, has_media=has_media)
    log.info(
        "TaskClassifier | session=%s | %s",
        session_id, _decision.rationale
    )

    # ── Fetch Unified Memory Context ──────────────────────────────────────────
    _original_message = message
    try:
        from core.memory import get_memory
        mem = get_memory()
        context_block = await mem.build_context_block(session_id)
        if context_block:
            message = f"{context_block}\n\n[NEW USER MESSAGE]\n{message}"
    except Exception as e:
        log.warning("Failed to inject memory context: %s", e)

    # Stage 2: LLM arbiter for borderline swarm decisions
    if _decision.swarm_confidence == "LOW" and ":swarm:" not in session_id:
        from core.task_classifier import should_use_swarm_async
        _decision.use_swarm = await should_use_swarm_async(_original_message, _decision)
        log.info(
            "SwarmArbiter refined swarm decision: use_swarm=%s",
            _decision.use_swarm,
        )

    # ── Explicit Swarm Override (Phase 3) ────────────────────────────────────
    # NEVER trigger a swarm if we are already inside a sub-agent!
    if _decision.use_swarm and ":swarm:" not in session_id:
        log.info("Swarm activated | session=%s | type=%s", session_id, _decision.task_type)
        try:
            from core.swarm import run_swarm
            result = await run_swarm(message, session_id, platform)
            try:
                from core.memory import get_memory
                import asyncio
                mem = get_memory()
                asyncio.ensure_future(mem.add_turn(
                    session_id, "user", _original_message,
                    provider=None, model=None
                ))
                asyncio.ensure_future(mem.add_turn(
                    session_id, "assistant", result,
                    provider=None, model=None
                ))
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

    # Temporarily override routing policy for this call if requested.
    # This is safe for our single-process async event loop — no other
    # coroutine can preempt us during the `await` block below.
    # TODO: Replace with a per-call policy kwarg in router.route() in a
    # future refactor to eliminate this global-state mutation entirely.
    _override_active = (
        routing_policy_override is not None
        and routing_policy_override.upper() != settings.routing_policy
    )
    if _override_active:
        _old_policy = router._settings.routing_policy
        router._settings.routing_policy = routing_policy_override.upper()
        try:
            response: AgentResponse = await router.route(
                session_id=session_id,
                message=message,
                platform=platform,
                force_provider=fp,
                has_media=has_media,
                image_data=image_data,
                image_mime=image_mime,
            )
        finally:
            router._settings.routing_policy = _old_policy
    else:
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

    try:
        from core.memory import get_memory
        import asyncio
        mem = get_memory()
        asyncio.ensure_future(mem.add_turn(
            session_id, "user", _original_message,
            provider=None, model=None
        ))
        asyncio.ensure_future(mem.add_turn(
            session_id, "assistant", content,
            provider=response.provider.value if hasattr(response, 'provider') else None,
            model=response.model_name if hasattr(response, 'model_name') else None
        ))
    except Exception as e:
        log.warning("Failed to save memory: %s", e)

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
    """
    Completely clear all conversation history for a session.
    Clears both the in-memory provider agent state AND the persistent UnifiedMemory database.
    """
    # 1. Clear in-memory model router state
    router = get_router()
    await router.clear_all_memory(session_id)
    
    # 2. Clear persistent UnifiedMemory (SQLite/PostgreSQL)
    from core.memory import get_memory
    mem = get_memory()
    await mem.clear_session(session_id)
    
    log.info("Memory fully cleared | session=%s", session_id)


async def get_status() -> dict:
    """Return live health status of all AI providers."""
    return await get_router().get_health_report_async()


async def get_free_providers() -> list[str]:
    """Return list of configured free/budget providers."""
    return await get_router().get_free_providers()


def get_task_classification(message: str, has_media: bool = False) -> str:
    """Return human-readable task classification for a message."""
    return classify_task(message, has_media=has_media).value
