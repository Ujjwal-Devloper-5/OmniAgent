"""
Unified Cross-Model Memory — OmniAgent Production v4
══════════════════════════════════════════════════════
Single source of truth for ALL conversation history.

Problem solved:
  Previously each AI backend (OpenRouter, Ollama, Gemini) stored its own
  separate SQLite checkpoint thread. When the router switched providers due
  to a rate-limit or failure, the new model started with ZERO context.

Solution:
  A shared conversation log table in the same SQLite DB. Every agent writes
  to it after responding. Every agent reads from it before responding (as
  injected context in the prompt). Model switches are completely invisible
  to the user — full context is always preserved.

Usage:
  from core.memory import UnifiedMemory
  mem = UnifiedMemory()  # singleton-safe
  await mem.add_turn(session_id, "user", "hello")
  await mem.add_turn(session_id, "assistant", "hi there!")
  history = await mem.get_history(session_id, max_turns=20)
  context_block = await mem.build_context_block(session_id)
"""

from __future__ import annotations

import json
import time
from typing import Optional

import aiosqlite

from core.logger import get_logger
from config import settings

log = get_logger(__name__)

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS unified_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content     TEXT    NOT NULL,
    provider    TEXT    DEFAULT NULL,
    model       TEXT    DEFAULT NULL,
    ts          REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);
CREATE INDEX IF NOT EXISTS idx_um_session_ts ON unified_memory(session_id, ts);
"""


class UnifiedMemory:
    """
    Async-safe unified conversation store for all AI providers.
    Each call opens its own short-lived aiosqlite connection (WAL mode).
    """

    _initialized: set[str] = set()  # Track which DB paths have been set up

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or settings.db_path

    async def _ensure_schema(self) -> None:
        if self._db_path in UnifiedMemory._initialized:
            return
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.executescript(_TABLE_DDL)
            await conn.commit()
        UnifiedMemory._initialized.add(self._db_path)
        log.info("UnifiedMemory schema ready at %s", self._db_path)

    async def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Append one message turn to the unified history."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute(
                "INSERT INTO unified_memory (session_id, role, content, provider, model) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, provider, model),
            )
            await conn.commit()

    async def get_history(
        self,
        session_id: str,
        max_turns: int = 40,
    ) -> list[dict]:
        """
        Return the last `max_turns` messages for this session, oldest first.
        Each dict: {role, content, provider, model, ts}
        """
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT role, content, provider, model, ts
                FROM unified_memory
                WHERE session_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (session_id, max_turns),
            )
            rows = await cursor.fetchall()
        # Return chronological order (oldest first)
        return [dict(r) for r in reversed(rows)]

    async def build_context_block(self, session_id: str, max_turns: int = 20) -> str:
        """
        Build a formatted context block to inject into the AI system prompt,
        summarizing recent conversation history across all providers.
        Returns empty string if no history exists.
        """
        history = await self.get_history(session_id, max_turns=max_turns)
        if not history:
            return ""

        lines = ["[CONVERSATION HISTORY — Cross-Model Context]"]
        for turn in history:
            role_label = "You" if turn["role"] == "assistant" else "User"
            model_tag = f" ({turn['model']}|{turn['provider']})" if turn.get("model") and turn.get("provider") else ""
            # Truncate very long messages for context efficiency
            content = turn["content"]
            if len(content) > 800:
                content = content[:800] + "...[truncated]"
            lines.append(f"{role_label}{model_tag}: {content}")
        lines.append("[END HISTORY]")
        return "\n".join(lines)

    async def clear_session(self, session_id: str) -> None:
        """Delete all history for a session (e.g. on !forget)."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "DELETE FROM unified_memory WHERE session_id = ?",
                (session_id,),
            )
            await conn.commit()
        log.info("UnifiedMemory cleared for session=%s", session_id)

    async def get_session_stats(self, session_id: str) -> dict:
        """Return stats about a session's memory."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM unified_memory WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        count, first_ts, last_ts = row
        return {
            "session_id": session_id,
            "total_turns": count or 0,
            "first_message_unix": first_ts,
            "last_message_unix": last_ts,
        }


# Module-level singleton
_memory: Optional[UnifiedMemory] = None

def get_memory() -> UnifiedMemory:
    """Return the global UnifiedMemory singleton."""
    global _memory
    if _memory is None:
        _memory = UnifiedMemory()
    return _memory
