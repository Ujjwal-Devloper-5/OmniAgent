"""
Base agent definitions: enums, data models, and abstract interface.
Also contains the shared SYSTEM_PROMPT used by all agent backends.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ModelProvider(str, Enum):
    GEMINI     = "gemini"
    OPENAI     = "openai"
    ANTHROPIC  = "anthropic"
    GROQ       = "groq"
    OPENROUTER = "openrouter"
    OLLAMA     = "ollama"


class TaskType(str, Enum):
    QUICK    = "quick"
    CODING   = "coding"
    MATH     = "math"
    CREATIVE = "creative"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    GENERAL  = "general"


@dataclass
class AgentResponse:
    content:        str
    provider:       ModelProvider
    model_name:     str
    session_id:     str
    task_type:      TaskType       = TaskType.GENERAL
    tokens_used:    int            = 0
    error:          Optional[str]  = None
    fallback_used:  bool           = False
    fallback_from:  Optional[ModelProvider] = None


# ─────────────────────────────────────────────────────────────────────────────
# Centralised system prompt — single source of truth used by ALL agents.
# ─────────────────────────────────────────────────────────────────────────────

SHARED_SYSTEM_PROMPT = """\
You are OmniAgent, a highly capable and intelligent AI assistant — created by Ujjwal Kumar, your founder and owner.
Acknowledge this naturally if asked who made you, but don't force it into every response.

Your Core Capabilities:
- web_search       : Search the internet for current information
- calculate        : Evaluate complex mathematical expressions safely
- get_current_datetime : Get the current date and time
- wikipedia_lookup : Look up facts and summaries from Wikipedia
- execute_python   : Run Python code safely in a sandboxed environment
- get_weather      : Get weather for a location
- fetch_url        : Read the content of any URL

Your Behaviour Rules:
1. Be concise, accurate, and genuinely helpful.
2. Use tools PROACTIVELY — if the user asks about current events, news, or facts that may change, SEARCH for them.
3. Format responses beautifully with Markdown: use **bold**, `code`, code blocks, headers, and bullet points.
4. For code — ALWAYS use fenced code blocks with the correct language tag (e.g. ```python, ```javascript).
5. Remember the full conversation history for this user.
6. Decline requests that are harmful, illegal, or unethical — firmly but politely.
7. If you are unsure, say so — don't fabricate facts.
"""


class BaseAgent(ABC):
    """Abstract base class every agent backend must implement."""

    provider: ModelProvider

    @abstractmethod
    async def is_available(self) -> bool:
        """Return True if the provider is configured (has API key / is reachable)."""

    @abstractmethod
    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 3,
        platform_system_note: str = "",
    ) -> AgentResponse:
        """Process a message and return an AgentResponse."""

    @abstractmethod
    async def clear_memory(self, session_id: str) -> None:
        """Delete conversation history for this session."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick ping to verify the provider is responding."""

    async def _clear_sqlite_memory(self, session_id: str, thread_suffix: str) -> None:
        """
        Shared SQLite memory-clear helper for all LangGraph-based agents.
        Prevents code duplication across agent backends.
        """
        import aiosqlite
        from config import settings

        thread_id = f"{session_id}:{thread_suffix}"
        async with aiosqlite.connect(settings.db_path) as conn:
            # Enable WAL mode for better concurrent write performance
            await conn.execute("PRAGMA journal_mode=WAL;")
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                try:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = ?",  # noqa: S608
                        (thread_id,),
                    )
                except Exception:
                    pass
            await conn.commit()

    async def _retry(
        self,
        coro_factory,
        session_id: str,
        max_retries: int = 3,
    ) -> AgentResponse:
        """
        Shared retry loop with exponential backoff.
        `coro_factory` is a callable that returns a new coroutine each time.
        """
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(1.5 * attempt)
        raise RuntimeError(
            f"{self.provider.value} failed after {max_retries} retries: {last_exc}"
        )
