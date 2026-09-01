"""
Smart Model Router v3 — Enterprise-Grade Intelligence
═══════════════════════════════════════════════════════

Routing Philosophy
──────────────────
  TIER 1 — Premium paid APIs (OpenAI, Anthropic)
      Best quality, use if keys present.

  TIER 2 — Free online APIs (Gemini FREE, Groq FREE, OpenRouter FREE)
      Excellent quality, zero system load, internet-dependent.
      YOUR PRIMARY TIER — you have Gemini + OpenRouter here.

  TIER 3 — Local Ollama
      Fully offline, uses YOUR hardware.
      Used as fallback when BOTH tier 1 & 2 fail,
      OR when the task REQUIRES local capability (e.g. media/vision
      and no online vision provider is configured or available).

Media / Vision Routing Intelligence
─────────────────────────────────────
  When a message contains a file or photo (has_media=True):
  1. Router checks which ONLINE providers support vision (Gemini, OpenAI, Anthropic)
  2. If any online vision provider is available → use it (smart, free, no GPU)
  3. If NO online vision provider is available BUT Ollama has qwen2.5vl installed
     → IMMEDIATELY route to local Ollama multimodal (zero wait, no hallucination)
  4. Never send vision tasks to Groq/OpenRouter (they cannot process media)
     → they become TEXT-ONLY fallbacks if everything else fails

Boot-Time Capability Detection
───────────────────────────────
  All providers probed in parallel at startup (8s timeout each).
  Ollama model capabilities detected from installed model list.
  Cached → zero latency per message routing.

Health Tracking
───────────────
  3 consecutive failures → provider quarantined for 5 minutes.
  Auto-recovers. Dead providers skipped instantly (no timeout wait).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.agents.base import AgentResponse, ModelProvider, TaskType
from core.logger import get_logger
from core.model_registry import get_registry

if TYPE_CHECKING:
    from core.agents.base import BaseAgent

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Per-platform max response length (characters)
# ─────────────────────────────────────────────────────────────────────────────
_PLATFORM_LIMITS: dict[str, int] = {
    "discord":  1900,
    "telegram": 4000,
}


# ─────────────────────────────────────────────────────────────────────────────
# Provider capability matrix
# ─────────────────────────────────────────────────────────────────────────────
# Defines what each provider's API/tier can handle.
# Ollama's vision capability is determined dynamically from installed models.

_PROVIDER_CAPS: dict[ModelProvider, set[str]] = {
    ModelProvider.OPENAI:     {"text", "vision", "coding", "math", "creative", "research", "analysis", "quick", "general"},
    ModelProvider.ANTHROPIC:  {"text", "vision", "creative", "analysis", "research", "coding", "math", "general", "quick"},
    ModelProvider.GEMINI:     {"text", "vision", "research", "math", "analysis", "coding", "creative", "general", "quick"},
    ModelProvider.GROQ:       {"text", "coding", "math", "creative", "research", "analysis", "general", "quick"},   # NO vision
    ModelProvider.OPENROUTER: {"text", "coding", "creative", "research", "general", "quick"},                       # NO vision on free tier
    ModelProvider.OLLAMA:     {"text", "general", "quick"},   # vision added dynamically if multimodal model installed
}

# Providers that CAN handle vision/media (checked against _PROVIDER_CAPS dynamically)
_VISION_CAPABLE = {"vision", "multimodal"}


# ─────────────────────────────────────────────────────────────────────────────
# Task Classifier — zero-latency, pure keyword heuristic
# ─────────────────────────────────────────────────────────────────────────────

_CODING_KEYWORDS = frozenset({
    "code", "function", "debug", "error", "bug", "script", "program", "class",
    "method", "implement", "algorithm", "python", "javascript", "typescript",
    "java", "rust", "golang", "cpp", "c++", "sql", "api", "regex", "compile",
    "refactor", "syntax", "library", "module", "import", "exception", "stack",
    "trace", "dockerfile", "yaml", "json", "html", "css", "bash", "shell",
    "git", "repository", "deploy", "kubernetes", "docker", "lambda", "async",
    "database", "orm", "flask", "fastapi", "django", "react", "vue", "angular",
    "fix", "solve", "implement", "build", "create function", "write a function",
})

_MATH_KEYWORDS = frozenset({
    "calculate", "compute", "math", "equation", "solve", "integral", "derivative",
    "matrix", "vector", "probability", "statistics", "formula", "theorem", "proof",
    "algebra", "calculus", "geometry", "trigonometry", "logarithm", "factorial",
    "series", "limit", "differential", "linear", "quadratic", "polynomial",
    "optimise", "maximize", "minimize", "distribution", "correlation", "variance",
    "regression", "hypothesis", "arithmetic", "percentage", "ratio", "fraction",
})

_CREATIVE_KEYWORDS = frozenset({
    "write", "story", "poem", "essay", "creative", "fiction", "novel", "chapter",
    "character", "plot", "narrative", "lyric", "song", "haiku", "sonnet",
    "brainstorm", "imagine", "invent", "roleplay", "metaphor", "analogy",
    "describe", "draft", "compose", "screenplay", "dialogue", "blog", "article",
    "summarize", "rewrite", "paraphrase", "tone", "style", "voice", "caption",
})

_RESEARCH_KEYWORDS = frozenset({
    "search", "find", "lookup", "who is", "what is", "when did", "where is",
    "latest", "recent", "news", "current", "today", "update", "wikipedia",
    "history", "biography", "fact", "information", "research", "source",
    "reference", "explain", "definition", "meaning", "origin", "founded",
    "invented", "discovered", "population", "capital", "president", "ceo",
    "tell me about", "what are", "how does",
})

_QUICK_PATTERNS = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no",
    "sure", "great", "good", "bye", "goodbye", "help", "what can you do",
    "how are you", "who are you", "ping", "test",
})


def classify_task(message: str, has_media: bool = False) -> TaskType:
    """
    Classify a message into a TaskType using keyword heuristics.
    Zero API calls, pure Python — called before any LLM interaction.
    """
    if has_media:
        return TaskType.VISION

    lower = message.lower().strip()

    # Very short common greetings → QUICK (use fastest provider)
    if len(lower) < 40 and any(lower.startswith(p) for p in _QUICK_PATTERNS):
        return TaskType.QUICK

    words = set(lower.split())
    scores = {
        TaskType.CODING:   len(words & _CODING_KEYWORDS),
        TaskType.MATH:     len(words & _MATH_KEYWORDS),
        TaskType.CREATIVE: len(words & _CREATIVE_KEYWORDS),
        TaskType.RESEARCH: len(words & _RESEARCH_KEYWORDS),
    }

    best_type, best_score = max(scores.items(), key=lambda x: x[1])
    if best_score >= 1:
        return best_type

    # Long messages → ANALYSIS
    if len(lower) > 300:
        return TaskType.ANALYSIS

    return TaskType.GENERAL


# ─────────────────────────────────────────────────────────────────────────────
# Task → Provider Preference Ordering
# ─────────────────────────────────────────────────────────────────────────────
#
# Design: ONLINE providers (Gemini, OpenRouter, Groq) always BEFORE local Ollama.
# Rationale: Online = free, no GPU, better intelligence. Local = fallback only.
# Exception: VISION tasks — online vision providers first, then Ollama multimodal.
#
# YOUR SETUP EFFECTIVE ROUTING:
#   You have: Gemini ✅  OpenRouter ✅  Ollama ✅
#   You lack: OpenAI ✗  Anthropic ✗  Groq ✗ (no key)
#
#   Text tasks: Gemini → OpenRouter → Ollama
#   Vision:     Gemini → Ollama (qwen2.5vl)   [OR has no vision on free]
#   Coding:     Gemini → OpenRouter → Ollama (qwen2.5-coder)
#   Quick:      Gemini → OpenRouter → Ollama

_TASK_PREFERENCES: dict[TaskType, list[ModelProvider]] = {
    # Coding: OpenAI GPT-4o best, Claude good, then Gemini, Groq (fast!), OpenRouter, Ollama-coder
    TaskType.CODING: [
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GEMINI,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Math: Gemini best reasoning + tools, OpenAI, Groq (fast), Claude, OpenRouter, Ollama
    TaskType.MATH: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.ANTHROPIC,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Creative: Claude best, Gemini, OpenAI, Groq, OpenRouter, Ollama
    TaskType.CREATIVE: [
        ModelProvider.ANTHROPIC,
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Research: Gemini (has search tools), OpenAI, Anthropic, Groq, OpenRouter, Ollama
    TaskType.RESEARCH: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Analysis: Gemini, Claude, OpenAI, Groq, OpenRouter, Ollama
    TaskType.ANALYSIS: [
        ModelProvider.GEMINI,
        ModelProvider.ANTHROPIC,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Quick: Groq (blazing fast LPU!), Gemini Flash, OpenRouter free, then others
    TaskType.QUICK: [
        ModelProvider.GROQ,
        ModelProvider.GEMINI,
        ModelProvider.OPENROUTER,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.OLLAMA,
    ],
    # General: Gemini default, then full online chain, Ollama last
    TaskType.GENERAL: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Vision/Media: online vision providers FIRST (they're better + free), then local multimodal
    # Groq and OpenRouter do NOT support vision → they are TEXT-ONLY fallbacks here
    TaskType.VISION: [
        ModelProvider.OPENAI,       # GPT-4o — multimodal, if key present
        ModelProvider.ANTHROPIC,    # Claude — multimodal, if key present
        ModelProvider.GEMINI,       # Gemini Flash — multimodal, FREE tier ✅
        ModelProvider.OLLAMA,       # qwen2.5vl — local multimodal, instant
        ModelProvider.GROQ,         # text-only fallback
        ModelProvider.OPENROUTER,   # text-only fallback
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Health State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _ProviderHealth:
    failures:     int   = 0
    last_failure: float = 0.0
    last_success: float = field(default_factory=time.monotonic)
    is_healthy:   bool  = True


# ─────────────────────────────────────────────────────────────────────────────
# Model Router
# ─────────────────────────────────────────────────────────────────────────────

class ModelRouter:
    """
    Routes messages to the best available AI provider.

    Core features:
    ─ Boot-time parallel provider probe → cached availability (zero per-msg latency)
    ─ Online-first routing (Gemini/OpenRouter/Groq before Ollama)
    ─ Media/vision aware routing (Gemini → Ollama multimodal)
    ─ Capability matrix per provider
    ─ Health tracking with quarantine + auto-recovery
    ─ Instant skip of unconfigured/dead providers
    ─ Ollama per-task model selection (coder, multimodal, reasoning, etc.)
    """

    def __init__(self) -> None:
        from config import settings
        from core.agents.anthropic_agent import AnthropicAgent
        from core.agents.gemini_agent import GeminiAgent
        from core.agents.groq_agent import GroqAgent
        from core.agents.ollama_agent import OllamaAgent
        from core.agents.openai_agent import OpenAIAgent
        from core.agents.openrouter_agent import OpenRouterAgent

        self._agents: dict[ModelProvider, BaseAgent] = {
            ModelProvider.GEMINI:     GeminiAgent(),
            ModelProvider.OPENAI:     OpenAIAgent(),
            ModelProvider.ANTHROPIC:  AnthropicAgent(),
            ModelProvider.GROQ:       GroqAgent(),
            ModelProvider.OPENROUTER: OpenRouterAgent(),
            ModelProvider.OLLAMA:     OllamaAgent(),
        }
        self._health: dict[ModelProvider, _ProviderHealth] = {
            p: _ProviderHealth() for p in ModelProvider
        }
        self._settings = settings
        self._lock = asyncio.Lock()

        # Cached boot-time availability
        self._cached_available: dict[ModelProvider, bool] = {
            p: False for p in ModelProvider
        }
        self._boot_probe_done: bool = False
        self._boot_probe_lock = asyncio.Lock()

        # Parse configured fallback order (from .env)
        fallback: list[ModelProvider] = []
        for name in settings.fallback_order_list:
            try:
                fallback.append(ModelProvider(name))
            except ValueError:
                log.warning("Unknown provider in FALLBACK_ORDER: '%s'", name)
        self._fallback_order = fallback or list(ModelProvider)

        log.info(
            "ModelRouter initialised | providers=%d fallback=%s",
            len(self._agents),
            [p.value for p in self._fallback_order],
        )

    # ── Boot-time parallel probe ───────────────────────────────────────────────

    async def probe_all_providers(self) -> None:
        """
        Probe all providers in parallel at boot time.
        Results are cached so route() calls have zero probe latency.
        Also detects Ollama vision capability dynamically.
        """
        async with self._boot_probe_lock:
            if self._boot_probe_done:
                return

            log.info("Boot probe: checking all providers in parallel...")

            async def _probe_one(provider: ModelProvider, agent) -> tuple[ModelProvider, bool]:
                try:
                    result = await asyncio.wait_for(agent.is_available(), timeout=8.0)
                    return provider, bool(result)
                except Exception as exc:
                    log.debug("Provider %s probe failed: %s", provider.value, exc)
                    return provider, False

            results = await asyncio.gather(
                *[_probe_one(p, a) for p, a in self._agents.items()],
                return_exceptions=False,
            )

            configured = []
            for provider, available in results:
                self._cached_available[provider] = available
                if available:
                    configured.append(provider.value)

            # Dynamically update Ollama capabilities based on installed models
            ollama_agent = self._agents.get(ModelProvider.OLLAMA)
            ollama_has_vision = False
            if ollama_agent and hasattr(ollama_agent, "has_vision_capability"):
                ollama_has_vision = ollama_agent.has_vision_capability()
                if ollama_has_vision:
                    _PROVIDER_CAPS[ModelProvider.OLLAMA] |= {"vision", "multimodal"}
                    log.info("Ollama: vision/multimodal capability detected (qwen2.5vl or similar)")

            self._boot_probe_done = True

            # ── Initialise the dynamic model registry ─────────────────────────
            # Build the set of provider names that are actually configured.
            available_provider_names: set[str] = {
                provider.value for provider, avail in results if avail
            }
            try:
                registry = get_registry()
                await registry.initialize(available_provider_names)

                # Feed Ollama's installed model list so unregistered models
                # get auto-registered with conservative default scores.
                if ollama_agent and hasattr(ollama_agent, "_model_capabilities"):
                    installed_names = list(ollama_agent._model_capabilities.keys())
                    await registry.add_ollama_models(installed_names)
            except Exception as reg_exc:
                log.warning("ModelRegistry init failed (non-fatal): %s", reg_exc)

            # Log the effective routing for this setup
            vision_providers = [
                p.value for p in ModelProvider
                if self._cached_available.get(p) and
                   (_PROVIDER_CAPS.get(p, set()) & _VISION_CAPABLE)
            ]

            log.info(
                "Boot probe complete | configured=[%s] | vision=[%s] | ollama_vision=%s",
                ", ".join(configured) or "NONE",
                ", ".join(vision_providers) or "none",
                ollama_has_vision,
            )

    # ── Health helpers ────────────────────────────────────────────────────────

    def _is_healthy(self, provider: ModelProvider) -> bool:
        h = self._health[provider]
        if h.is_healthy:
            return True
        elapsed = time.monotonic() - h.last_failure
        if elapsed >= self._settings.model_recovery_seconds:
            log.info("Provider %s auto-recovered after %.0fs", provider.value, elapsed)
            h.is_healthy = True
            h.failures = 0
            return True
        return False

    def _record_success(self, provider: ModelProvider) -> None:
        h = self._health[provider]
        h.failures = 0
        h.is_healthy = True
        h.last_success = time.monotonic()
        self._cached_available[provider] = True

    def _record_failure(self, provider: ModelProvider) -> None:
        h = self._health[provider]
        h.failures += 1
        h.last_failure = time.monotonic()
        if h.failures >= self._settings.model_failure_threshold:
            if h.is_healthy:
                log.warning(
                    "Provider %s marked UNHEALTHY after %d consecutive failures",
                    provider.value, h.failures,
                )
            h.is_healthy = False

    def _get_available_providers(self) -> list[ModelProvider]:
        """
        Return providers that are configured AND healthy.
        Uses boot-time cache — ZERO network calls per message.
        """
        return [
            p for p in ModelProvider
            if self._cached_available.get(p, False) and self._is_healthy(p)
        ]

    def _build_priority_list(
        self,
        task_type: TaskType,
        available: list[ModelProvider],
        needs_vision: bool = False,
    ) -> list[ModelProvider]:
        """
        Build ordered provider list for this task, filtered to available providers.

        For vision tasks:
        - Vision-capable providers come FIRST
        - Text-only providers come AFTER as fallbacks (they'll handle it as text)

        Always: online providers before local Ollama (except vision tasks where
        local multimodal may be better than online text-only providers).
        """
        preferred = _TASK_PREFERENCES.get(task_type, list(ModelProvider))

        if needs_vision:
            # Split: vision-capable vs text-only
            vision_first = [
                p for p in preferred
                if p in available and (_PROVIDER_CAPS.get(p, set()) & _VISION_CAPABLE)
            ]
            text_fallbacks = [
                p for p in preferred
                if p in available and p not in vision_first
            ]
            ordered = vision_first + text_fallbacks
        else:
            ordered = [p for p in preferred if p in available]

        # Append any available provider not already in the list
        for p in self._fallback_order:
            if p in available and p not in ordered:
                ordered.append(p)

        return ordered

    def _select_best_model_for_task(
        self,
        task_type: TaskType,
        available: list[ModelProvider],
        needs_vision: bool = False,
        needs_tools: bool = False,
    ) -> list[ModelProvider]:
        """
        Build an ordered provider list using the dynamic ModelRegistry.

        Queries the registry for all eligible models scored by
        intelligence/speed/tool_reliability, then maps each model's provider
        to the agents dict.  Models from unavailable providers are skipped.

        Falls back to ``_build_priority_list`` (legacy _TASK_PREFERENCES) if
        the registry returns no candidates — so the system degrades gracefully
        even if models.json is missing or empty.

        Parameters
        ----------
        task_type    : Classified task (CODING, MATH, VISION, etc.)
        available    : Providers confirmed available at boot probe.
        needs_vision : Whether the request includes media / requires vision.
        needs_tools  : Whether tool-calling reliability should be weighted.
        """
        try:
            registry = get_registry()
            ranked = registry.get_ranked_list(
                task_type=task_type.value,
                needs_vision=needs_vision,
                needs_tools=needs_tools,
            )
        except Exception as exc:
            log.warning(
                "_select_best_model_for_task: registry error (%s) — using legacy list",
                exc,
            )
            return self._build_priority_list(task_type, available, needs_vision)

        if not ranked:
            log.debug(
                "_select_best_model_for_task: registry empty for task=%s — using legacy list",
                task_type.value,
            )
            return self._build_priority_list(task_type, available, needs_vision)

        # Map scored models → provider enum, deduplicate, skip unavailable
        seen: set[ModelProvider] = set()
        ordered: list[ModelProvider] = []
        for model_entry in ranked:
            try:
                provider = ModelProvider(model_entry.provider)
            except ValueError:
                continue  # Unknown provider in registry entry — skip
            if provider not in available or provider in seen:
                continue
            seen.add(provider)
            ordered.append(provider)

        if not ordered:
            # All registry-recommended providers are unavailable right now
            log.debug(
                "_select_best_model_for_task: all registry providers unavailable — using legacy list",
            )
            return self._build_priority_list(task_type, available, needs_vision)

        # Append any remaining available providers not yet in list (full fallback chain)
        for p in self._build_priority_list(task_type, available, needs_vision):
            if p not in seen:
                ordered.append(p)

        log.debug(
            "_select_best_model_for_task | task=%s → %s",
            task_type.value,
            [p.value for p in ordered],
        )
        return ordered

    # ── Main routing entry point ───────────────────────────────────────────────

    async def route(
        self,
        session_id:     str,
        message:        str,
        platform:       str = "unknown",
        force_provider: Optional[ModelProvider] = None,
        has_media:      bool = False,
        image_data:     bytes | None = None,
        image_mime:     str = "image/jpeg",
    ) -> AgentResponse:
        """
        Route a message to the best available provider.

        Intelligence:
        1. Lazy boot probe if not done yet (only first call)
        2. Classify task (VISION if media present)
        3. For vision: online vision providers → local multimodal → text fallback
        4. For text: premium tier → free online → local Ollama
        5. Skip unhealthy/unconfigured instantly
        6. Fallback through chain until one succeeds

        image_data: downloaded image bytes passed to vision-capable providers.
        image_mime: MIME type of the image (e.g. 'image/png').
        """
        # Lazy boot probe on first message (subsequent calls use cache)
        if not self._boot_probe_done:
            await self.probe_all_providers()

        task_type = classify_task(message, has_media=has_media)

        needs_vision = has_media or task_type == TaskType.VISION
        available = self._get_available_providers()

        log.info(
            "Routing | session=%s platform=%s task=%s media=%s available=[%s]",
            session_id, platform, task_type.value, has_media,
            ", ".join(p.value for p in available),
        )

        if not available:
            # Emergency: re-probe Ollama (it might be starting up)
            ollama = self._agents[ModelProvider.OLLAMA]
            try:
                if await asyncio.wait_for(ollama.is_available(), timeout=5.0):
                    self._cached_available[ModelProvider.OLLAMA] = True
                    available = [ModelProvider.OLLAMA]
                    log.info("Ollama came online during emergency probe")
            except Exception:
                pass

        if not available:
            raise RuntimeError(
                "❌ No AI providers are available!\n"
                "At least one must be configured:\n"
                "  • GEMINI_API_KEY  → https://aistudio.google.com  (free)\n"
                "  • OPENROUTER_API_KEY → https://openrouter.ai     (free)\n"
                "  • GROQ_API_KEY    → https://console.groq.com     (free)\n"
                "  • Or run Ollama locally (fully offline)\n"
                "See .env.example for setup instructions."
            )

        if force_provider:
            if force_provider in available:
                priority = [force_provider]
                for p in self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True
                ):
                    if p not in priority:
                        priority.append(p)
            else:
                log.warning(
                    "Forced provider %s not available, using auto-routing",
                    force_provider.value,
                )
                priority = self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True
                )
        else:
            # Registry-first: scored dynamic pool, falls back to _TASK_PREFERENCES
            priority = self._select_best_model_for_task(
                task_type, available, needs_vision, needs_tools=True
            )

        if not priority:
            raise RuntimeError("No valid providers in priority list.")

        first_choice = priority[0]
        last_error: Exception | None = None

        # Platform-aware system suffix (not stored in memory)
        char_limit = _PLATFORM_LIMITS.get(platform)
        platform_system_note = (
            f"\n\n[PLATFORM CONSTRAINT — {platform.upper()}]: "
            f"Keep your response under {char_limit} characters. "
            f"Be concise. If showing code, keep it short but complete."
            if char_limit else ""
        )

        for i, provider in enumerate(priority):
            agent = self._agents[provider]
            is_fallback = (i > 0)

            # Determine if this specific provider can handle vision
            provider_caps = _PROVIDER_CAPS.get(provider, set())
            effective_vision = needs_vision and bool(provider_caps & _VISION_CAPABLE)

            log.info(
                "Trying provider=%s task=%s vision=%s (%d/%d)%s",
                provider.value, task_type.value, effective_vision,
                i + 1, len(priority),
                " [FALLBACK]" if is_fallback else "",
            )

            try:
                response = await agent.process_message(
                    session_id=session_id,
                    message=message,
                    platform=platform,
                    task_type=task_type,
                    platform_system_note=platform_system_note,
                    needs_vision=effective_vision,
                    image_data=image_data if effective_vision else None,
                    image_mime=image_mime,
                )
                self._record_success(provider)
                # Inform registry of success so it can reset health demotions
                try:
                    get_registry().record_success(response.model_name)
                except Exception:
                    pass

                if is_fallback:
                    response.fallback_used = True
                    response.fallback_from = first_choice

                response.has_media = has_media

                log.info(
                    "✓ Success | provider=%s model=%s task=%s fallback=%s tokens≈%d",
                    provider.value, response.model_name,
                    task_type.value, response.fallback_used, response.tokens_used,
                )
                return response

            except Exception as exc:
                log.error("✗ Provider %s failed: %s", provider.value, exc)
                self._record_failure(provider)
                # Inform registry so it can score-demote the specific model
                try:
                    # Best effort: find the model_id for this provider from registry
                    reg = get_registry()
                    ranked = reg.get_ranked_list(task_type.value, needs_vision, True)
                    for m in ranked:
                        if m.provider == provider.value:
                            reg.record_failure(m.id)
                            break
                except Exception:
                    pass
                last_error = exc
                continue

        raise RuntimeError(
            f"All providers failed. Last error: {last_error}\n"
            f"Tried: {[p.value for p in priority]}"
        )

    # ── Memory management ─────────────────────────────────────────────────────

    async def clear_all_memory(self, session_id: str) -> None:
        for provider, agent in self._agents.items():
            try:
                await agent.clear_memory(session_id)
            except Exception as exc:
                log.warning("Failed to clear %s memory: %s", provider.value, exc)

    # ── Status reporting ──────────────────────────────────────────────────────

    async def get_health_report_async(self) -> dict:
        """Return live health status of all providers including capabilities."""
        report = {}
        for provider, health in self._health.items():
            configured = self._cached_available.get(provider, False)
            currently_healthy = self._is_healthy(provider) and configured
            caps = sorted(_PROVIDER_CAPS.get(provider, set()))
            report[provider.value] = {
                "configured":           configured,
                "healthy":              currently_healthy,
                "consecutive_failures": health.failures,
                "failure_threshold":    self._settings.model_failure_threshold,
                "capabilities":         caps,
                "has_vision":           bool(_PROVIDER_CAPS.get(provider, set()) & _VISION_CAPABLE),
            }
        return report

    async def get_free_providers(self) -> list[str]:
        """Return list of configured free/budget providers."""
        free = []
        for p in [ModelProvider.GROQ, ModelProvider.OPENROUTER, ModelProvider.OLLAMA]:
            if self._cached_available.get(p, False):
                free.append(p.value)
        return free

    def get_ollama_vision_model(self) -> Optional[str]:
        """Return name of installed Ollama vision model, if any."""
        agent = self._agents.get(ModelProvider.OLLAMA)
        if agent and hasattr(agent, "get_vision_model"):
            return agent.get_vision_model()
        return None

    async def refresh_provider_cache(self) -> None:
        """Re-probe all providers (useful after config changes)."""
        self._boot_probe_done = False
        await self.probe_all_providers()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    """Get the global ModelRouter singleton (lazy init)."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
