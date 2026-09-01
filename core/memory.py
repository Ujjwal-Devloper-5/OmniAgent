"""
Unified conversation memory store.

Backend selection (automatic at startup):
  • PostgreSQL  — when DATABASE_URL env var is set (production mode)
  • SQLite      — fallback for local / single-user installs

The public API (add_turn, get_history, build_context_block, clear_session,
get_session_stats) is identical regardless of backend.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from config import settings
from core.logger import get_logger

log = get_logger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS unified_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    provider   TEXT,
    model      TEXT,
    ts         INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_um_session_ts ON unified_memory(session_id, ts);
"""

_PG_DDL = """
CREATE TABLE IF NOT EXISTS unified_memory (
    id         BIGSERIAL PRIMARY KEY,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    provider   TEXT,
    model      TEXT,
    ts         BIGINT  NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);
CREATE INDEX IF NOT EXISTS idx_um_session_ts ON unified_memory(session_id, ts);
"""


class UnifiedMemory:
    """
    Async-safe unified conversation store for all AI providers.
    Automatically selects PostgreSQL or SQLite backend based on settings.
    """

    _initialized: set[str] = set()
    _pg_pool = None  # asyncpg connection pool (shared)
    _pg_pool_lock = asyncio.Lock()

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or settings.db_path
        self._use_postgres = settings.use_postgres

    # ── PostgreSQL backend ────────────────────────────────────────────────────

    async def _get_pg_pool(self):
        """Return (creating if needed) the shared asyncpg connection pool."""
        async with UnifiedMemory._pg_pool_lock:
            if UnifiedMemory._pg_pool is None:
                import asyncpg
                # Strip the +asyncpg dialect prefix if present
                url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
                UnifiedMemory._pg_pool = await asyncpg.create_pool(
                    url,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                )
                log.info("asyncpg pool created | url=%s", url.split("@")[-1])
            return UnifiedMemory._pg_pool

    async def _ensure_schema_pg(self) -> None:
        """Create the unified_memory table in PostgreSQL if not present."""
        key = f"pg:{settings.database_url}"
        if key in UnifiedMemory._initialized:
            return
        pool = await self._get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(_PG_DDL)
        UnifiedMemory._initialized.add(key)
        log.info("UnifiedMemory PostgreSQL schema ready")

    async def _add_turn_pg(self, session_id, role, content, provider, model) -> None:
        await self._ensure_schema_pg()
        pool = await self._get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO unified_memory (session_id, role, content, provider, model) "
                "VALUES ($1, $2, $3, $4, $5)",
                session_id, role, content, provider, model,
            )

    async def _get_history_pg(self, session_id, max_turns) -> list[dict]:
        await self._ensure_schema_pg()
        pool = await self._get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role, content, provider, model, ts "
                "FROM unified_memory "
                "WHERE session_id = $1 "
                "ORDER BY ts DESC LIMIT $2",
                session_id, max_turns,
            )
        return [dict(r) for r in reversed(rows)]

    async def _clear_session_pg(self, session_id) -> None:
        await self._ensure_schema_pg()
        pool = await self._get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM unified_memory WHERE session_id = $1", session_id
            )

    async def _get_stats_pg(self, session_id) -> dict:
        await self._ensure_schema_pg()
        pool = await self._get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM unified_memory WHERE session_id = $1",
                session_id,
            )
        return {
            "session_id": session_id,
            "total_turns": row[0] or 0,
            "first_message_unix": row[1],
            "last_message_unix": row[2],
        }

    # ── SQLite backend ────────────────────────────────────────────────────────

    async def _ensure_schema_sqlite(self) -> None:
        if self._db_path in UnifiedMemory._initialized:
            return
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.executescript(_SQLITE_DDL)
            await conn.commit()
        UnifiedMemory._initialized.add(self._db_path)
        log.info("UnifiedMemory SQLite schema ready at %s", self._db_path)

    async def _add_turn_sqlite(self, session_id, role, content, provider, model) -> None:
        await self._ensure_schema_sqlite()
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute(
                "INSERT INTO unified_memory (session_id, role, content, provider, model) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, provider, model),
            )
            await conn.commit()

    async def _get_history_sqlite(self, session_id, max_turns) -> list[dict]:
        await self._ensure_schema_sqlite()
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT role, content, provider, model, ts FROM unified_memory "
                "WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
                (session_id, max_turns),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def _clear_session_sqlite(self, session_id) -> None:
        await self._ensure_schema_sqlite()
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("DELETE FROM unified_memory WHERE session_id = ?", (session_id,))
            await conn.commit()

    async def _get_stats_sqlite(self, session_id) -> dict:
        await self._ensure_schema_sqlite()
        import aiosqlite
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

    # ── Public API (backend-agnostic) ─────────────────────────────────────────

    async def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Append one message turn to the unified history."""
        if self._use_postgres:
            await self._add_turn_pg(session_id, role, content, provider, model)
        else:
            await self._add_turn_sqlite(session_id, role, content, provider, model)

    async def get_history(self, session_id: str, max_turns: int = 40) -> list[dict]:
        """Return the last `max_turns` messages for this session, oldest first."""
        if self._use_postgres:
            return await self._get_history_pg(session_id, max_turns)
        return await self._get_history_sqlite(session_id, max_turns)

    async def build_context_block(self, session_id: str, max_turns: int = 20) -> str:
        """Build a formatted context block for AI system prompt injection."""
        history = await self.get_history(session_id, max_turns=max_turns)
        if not history:
            return ""
        lines = ["[CONVERSATION HISTORY — Cross-Model Context]"]
        for turn in history:
            role_label = "You" if turn["role"] == "assistant" else "User"
            model_tag = (
                f" ({turn['model']}|{turn['provider']})"
                if turn.get("model") and turn.get("provider")
                else ""
            )
            content = turn["content"]
            if len(content) > 800:
                content = content[:800] + "...[truncated]"
            lines.append(f"{role_label}{model_tag}: {content}")
        lines.append("[END HISTORY]")
        return "\n".join(lines)

    async def clear_session(self, session_id: str) -> None:
        """Delete all history for a session."""
        if self._use_postgres:
            await self._clear_session_pg(session_id)
        else:
            await self._clear_session_sqlite(session_id)
        log.info("UnifiedMemory cleared for session=%s", session_id)

    async def get_session_stats(self, session_id: str) -> dict:
        """Return stats about a session's memory."""
        if self._use_postgres:
            return await self._get_stats_pg(session_id)
        return await self._get_stats_sqlite(session_id)

    async def get_all_sessions(self) -> list[dict]:
        """Return summary of all active sessions — used by Admin Dashboard."""
        if self._use_postgres:
            pool = await self._get_pg_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT session_id, COUNT(*) as total_turns, "
                    "MIN(ts) as first_ts, MAX(ts) as last_ts "
                    "FROM unified_memory GROUP BY session_id ORDER BY last_ts DESC"
                )
            return [dict(r) for r in rows]
        else:
            import aiosqlite
            await self._ensure_schema_sqlite()
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(
                    "SELECT session_id, COUNT(*) as total_turns, "
                    "MIN(ts) as first_ts, MAX(ts) as last_ts "
                    "FROM unified_memory GROUP BY session_id ORDER BY last_ts DESC"
                )
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# Module-level singleton
_memory: Optional[UnifiedMemory] = None

def get_memory() -> UnifiedMemory:
    """Return the global UnifiedMemory singleton."""
    global _memory
    if _memory is None:
        _memory = UnifiedMemory()
    return _memory
