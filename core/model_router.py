"""
Smart Model Router — the brain of the multi-agent system.

Supports 6 providers:
  - Gemini     (Google, paid — best reasoning & research)
  - OpenAI     (paid — best coding)
  - Anthropic  (paid — best creative writing)
  - Groq       (FREE — ultra-fast Llama/Mixtral/Gemma via LPU)
  - OpenRouter (FREE tier — 200+ models including Llama, Mistral, DeepSeek)
  - Ollama     (FREE — fully local/offline fallback)

Routing logic:
  1. Zero-latency keyword-based task classification
  2. Task → preferred provider order mapping
  3. Skip unconfigured or unhealthy providers
  4. Full fallback chain until one succeeds
  5. Background health monitor keeps state fresh
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.agents.base import AgentResponse, ModelProvider, TaskType
from core.logger import get_logger

if TYPE_CHECKING:
    from core.agents.base import BaseAgent

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Per-platform max response length (characters) injected as system instructions
# ─────────────────────────────────────────────────────────────────────────────
_PLATFORM_LIMITS: dict[str, int] = {
    "discord":  1900,
    "telegram": 4000,
}


# ─────────────────────────────────────────────────────────────────────────────
# Task Classifier — zero latency, pure keyword scoring
# ─────────────────────────────────────────────────────────────────────────────

_CODING_KEYWORDS = frozenset({
    "code", "function", "debug", "error", "bug", "script", "program", "class",
    "method", "implement", "algorithm", "python", "javascript", "typescript",
    "java", "rust", "golang", "cpp", "c++", "sql", "api", "regex", "compile",
    "refactor", "syntax", "library", "module", "import", "exception", "stack",
    "trace", "dockerfile", "yaml", "json", "html", "css", "bash", "shell",
    "git", "repository", "deploy", "kubernetes", "docker", "lambda", "async",
    "database", "orm", "flask", "fastapi", "django", "react", "vue", "angular",
})

_MATH_KEYWORDS = frozenset({
    "calculate", "compute", "math", "equation", "solve", "integral", "derivative",
    "matrix", "vector", "probability", "statistics", "formula", "theorem", "proof",
    "algebra", "calculus", "geometry", "trigonometry", "logarithm", "factorial",
    "series", "limit", "differential", "linear", "quadratic", "polynomial",
    "optimise", "maximize", "minimize", "distribution", "correlation", "variance",
    "standard deviation", "regression", "hypothesis",
})

_CREATIVE_KEYWORDS = frozenset({
    "write", "story", "poem", "essay", "creative", "fiction", "novel", "chapter",
    "character", "plot", "narrative", "lyric", "song", "haiku", "sonnet",
    "brainstorm", "imagine", "invent", "roleplay", "metaphor", "analogy",
    "describe", "draft", "compose", "screenplay", "dialogue", "blog", "article",
    "summarize", "rewrite", "paraphrase", "tone", "style", "voice",
})

_RESEARCH_KEYWORDS = frozenset({
    "search", "find", "lookup", "who is", "what is", "when did", "where is",
    "latest", "recent", "news", "current", "today", "update", "wikipedia",
    "history", "biography", "fact", "information", "research", "source",
    "reference", "explain", "definition", "meaning", "origin", "founded",
    "invented", "discovered", "population", "capital", "president", "ceo",
})

_QUICK_PATTERNS = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no",
    "sure", "great", "good", "bye", "goodbye", "help", "what can you do",
    "how are you", "who are you", "ping",
})


def classify_task(message: str) -> TaskType:
    """
    Classify a message into a TaskType using keyword heuristics.
    Fast — zero API calls, pure Python.

    Returns
    -------
    TaskType
        The best-matching task category.
    """
    lower = message.lower().strip()

    # Very short messages / common greetings → QUICK
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

    # Long messages tend to need more analysis
    if len(lower) > 300:
        return TaskType.ANALYSIS

    return TaskType.GENERAL


# ─────────────────────────────────────────────────────────────────────────────
# Provider preference map — best provider per task type
# Order matters: first available + healthy one wins
# ─────────────────────────────────────────────────────────────────────────────

_TASK_PREFERENCES: dict[TaskType, list[ModelProvider]] = {
    # Code: OpenAI GPT-4o best, Groq Llama fast, Gemini, then free fallbacks
    TaskType.CODING: [
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.GEMINI,
        ModelProvider.ANTHROPIC,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Math: Gemini best for reasoning, then OpenAI, Groq Llama 70B
    TaskType.MATH: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.ANTHROPIC,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Creative: Claude best, then Gemini, OpenAI
    TaskType.CREATIVE: [
        ModelProvider.ANTHROPIC,
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Research: Gemini (best with tools), then OpenAI
    TaskType.RESEARCH: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Analysis: Gemini, Claude, OpenAI
    TaskType.ANALYSIS: [
        ModelProvider.GEMINI,
        ModelProvider.ANTHROPIC,
        ModelProvider.OPENAI,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
    ],
    # Quick: Groq is blazing fast (free!), then Gemini Flash, OpenRouter free
    TaskType.QUICK: [
        ModelProvider.GROQ,
        ModelProvider.GEMINI,
        ModelProvider.OPENROUTER,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.OLLAMA,
    ],
    # General: Gemini as default, then full fallback chain
    TaskType.GENERAL: [
        ModelProvider.GEMINI,
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GROQ,
        ModelProvider.OPENROUTER,
        ModelProvider.OLLAMA,
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
    Routes messages to the best available AI provider with automatic fallback.

    Features:
    ─ Zero-latency task classification
    ─ Per-task provider preference ordering  
    ─ Health tracking with automatic recovery
    ─ Full fallback chain across all 6 providers
    ─ Configurable via settings.fallback_order
    ─ Supports forcing a specific provider
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

        # Parse configured fallback order
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

    async def _available_providers(self) -> list[ModelProvider]:
        """Return providers that are configured AND currently healthy."""
        result = []
        for provider, agent in self._agents.items():
            if await agent.is_available() and self._is_healthy(provider):
                result.append(provider)
        return result

    def _priority_list(
        self,
        task_type: TaskType,
        available: list[ModelProvider],
    ) -> list[ModelProvider]:
        """Build ordered provider list for this task, filtered to available."""
        preferred = _TASK_PREFERENCES.get(task_type, list(ModelProvider))
        ordered = [p for p in preferred if p in available]
        # Append any available provider not in the preferred list
        for p in self._fallback_order:
            if p in available and p not in ordered:
                ordered.append(p)
        return ordered

    # ── Main routing entry point ───────────────────────────────────────────────

    async def route(
        self,
        session_id:     str,
        message:        str,
        platform:       str = "unknown",
        force_provider: Optional[ModelProvider] = None,
    ) -> AgentResponse:
        """
        Route a message to the best available provider.

        Parameters
        ----------
        session_id     : Unique conversation thread ID.
        message        : The user's message text.
        platform       : Platform name for logging.
        force_provider : Skip routing and use this provider directly.

        Returns
        -------
        AgentResponse from whichever provider succeeded.

        Raises
        ------
        RuntimeError if ALL providers fail or none are configured.
        """
        task_type = classify_task(message)
        available = await self._available_providers()

        log.info(
            "Routing | session=%s platform=%s task=%s available=[%s]",
            session_id, platform, task_type.value,
            ", ".join(p.value for p in available),
        )

        if not available:
            raise RuntimeError(
                "❌ No AI providers are available!\n"
                "Configure at least one of:\n"
                "  • GEMINI_API_KEY (https://aistudio.google.com)\n"
                "  • GROQ_API_KEY   (https://console.groq.com) ← FREE\n"
                "  • OPENROUTER_API_KEY (https://openrouter.ai) ← FREE tier\n"
                "  • OPENAI_API_KEY / ANTHROPIC_API_KEY\n"
                "  • Or run Ollama locally (fully offline)"
            )

        if force_provider:
            if force_provider in available:
                priority = [force_provider]
                # Add fallbacks after the forced one
                for p in self._priority_list(task_type, available):
                    if p not in priority:
                        priority.append(p)
            else:
                log.warning(
                    "Forced provider %s not available, using auto-routing",
                    force_provider.value,
                )
                priority = self._priority_list(task_type, available)
        else:
            priority = self._priority_list(task_type, available)

        first_choice = priority[0] if priority else None
        last_error: Exception | None = None

        # Build a platform-aware system suffix that is NOT stored in history.
        # We pass it as a separate system turn, not appended to the user message.
        char_limit = _PLATFORM_LIMITS.get(platform)
        if char_limit:
            platform_system_note = (
                f"\n\n[PLATFORM CONSTRAINT — {platform.upper()}]: "
                f"Keep your response under {char_limit} characters. "
                f"Be concise. If showing code, keep it short but complete."
            )
        else:
            platform_system_note = ""

        for i, provider in enumerate(priority):
            agent = self._agents[provider]
            is_fallback = (i > 0)

            log.info(
                "Trying provider=%s fallback=%s (%d/%d)",
                provider.value, is_fallback, i + 1, len(priority),
            )

            try:
                response = await agent.process_message(
                    session_id=session_id,
                    message=message,
                    platform=platform,
                    task_type=task_type,
                    platform_system_note=platform_system_note,
                )
                self._record_success(provider)

                if is_fallback and first_choice:
                    response.fallback_used = True
                    response.fallback_from = first_choice

                log.info(
                    "Success | provider=%s model=%s fallback=%s tokens≈%d",
                    provider.value, response.model_name,
                    response.fallback_used, response.tokens_used,
                )
                return response

            except Exception as exc:
                log.error("Provider %s failed: %s", provider.value, exc)
                self._record_failure(provider)
                last_error = exc
                continue

        raise RuntimeError(
            f"All providers failed. Last error: {last_error}\n"
            f"Tried: {[p.value for p in priority]}"
        )

    # ── Memory management ─────────────────────────────────────────────────────

    async def clear_all_memory(self, session_id: str) -> None:
        """Clear memory for a session across ALL providers."""
        for provider, agent in self._agents.items():
            try:
                await agent.clear_memory(session_id)
            except Exception as exc:
                log.warning("Failed to clear %s memory: %s", provider.value, exc)

    # ── Status reporting ──────────────────────────────────────────────────────

    async def get_health_report_async(self) -> dict:
        """Return live health status of all providers."""
        report = {}
        for provider, health in self._health.items():
            agent = self._agents[provider]
            configured = await agent.is_available()
            currently_healthy = self._is_healthy(provider) and configured
            report[provider.value] = {
                "configured":            configured,
                "healthy":               currently_healthy,
                "consecutive_failures":  health.failures,
                "failure_threshold":     self._settings.model_failure_threshold,
            }
        return report

    async def get_free_providers(self) -> list[str]:
        """Return list of configured free/budget providers."""
        free = []
        for p in [ModelProvider.GROQ, ModelProvider.OPENROUTER, ModelProvider.OLLAMA]:
            if await self._agents[p].is_available():
                free.append(p.value)
        return free


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
