"""
Per-user AI configuration store.

Stores custom system prompts, preferred providers, and feature flags
per user_id. Supports both PostgreSQL and SQLite backends.
"""
from __future__ import annotations

from typing import Optional
from config import settings
from core.logger import get_logger

log = get_logger(__name__)

_PG_DDL = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id         TEXT PRIMARY KEY,
    platform        TEXT NOT NULL DEFAULT 'discord',
    system_prompt   TEXT,
    preferred_model TEXT,
    language        TEXT DEFAULT 'auto',
    created_at      BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
    updated_at      BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
);
"""

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id         TEXT PRIMARY KEY,
    platform        TEXT NOT NULL DEFAULT 'discord',
    system_prompt   TEXT,
    preferred_model TEXT,
    language        TEXT DEFAULT 'auto',
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""


class UserSettingsStore:
    """Async-safe per-user settings store with dual PostgreSQL/SQLite backend."""

    _initialized: bool = False

    async def _ensure_schema(self) -> None:
        if UserSettingsStore._initialized:
            return
        if settings.use_postgres:
            from core.memory import get_memory
            pool = await get_memory()._get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute(_PG_DDL)
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.db_path) as conn:
                await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.executescript(_SQLITE_DDL)
                await conn.commit()
        UserSettingsStore._initialized = True
        log.info("UserSettings schema ready (backend=%s)", "postgres" if settings.use_postgres else "sqlite")

    async def get(self, user_id: str) -> dict:
        """Get settings for a user. Returns defaults if user not found."""
        await self._ensure_schema()
        defaults = {
            "user_id": user_id,
            "system_prompt": None,
            "preferred_model": None,
            "language": "auto",
            "platform": "unknown",
        }
        try:
            if settings.use_postgres:
                from core.memory import get_memory
                pool = await get_memory()._get_pg_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM user_settings WHERE user_id = $1", user_id
                    )
                return dict(row) if row else defaults
            else:
                import aiosqlite
                async with aiosqlite.connect(settings.db_path) as conn:
                    conn.row_factory = aiosqlite.Row
                    cursor = await conn.execute(
                        "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
                    )
                    row = await cursor.fetchone()
                return dict(row) if row else defaults
        except Exception as exc:
            log.warning("UserSettings.get failed for %s: %s", user_id, exc)
            return defaults

    async def upsert(self, user_id: str, platform: str = "discord", **kwargs) -> None:
        """Create or update settings for a user."""
        await self._ensure_schema()
        allowed_fields = {"system_prompt", "preferred_model", "language"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return
        try:
            if settings.use_postgres:
                from core.memory import get_memory
                pool = await get_memory()._get_pg_pool()
                set_clause = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
                values = [user_id, platform] + list(updates.values())
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"INSERT INTO user_settings (user_id, platform, {', '.join(updates.keys())}) "
                        f"VALUES ($1, $2, {', '.join(f'${i+3}' for i in range(len(updates)))}) "
                        f"ON CONFLICT (user_id) DO UPDATE SET {set_clause}, updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT",
                        *values,
                    )
            else:
                import aiosqlite
                import time as _time
                async with aiosqlite.connect(settings.db_path) as conn:
                    fields = ["user_id", "platform"] + list(updates.keys())
                    placeholders = ", ".join(["?"] * len(fields))
                    values_tuple = tuple([user_id, platform] + list(updates.values()))
                    await conn.execute(
                        f"INSERT OR REPLACE INTO user_settings ({', '.join(fields)}) VALUES ({placeholders})",
                        values_tuple,
                    )
                    await conn.commit()
        except Exception as exc:
            log.error("UserSettings.upsert failed for %s: %s", user_id, exc)

    async def delete(self, user_id: str) -> None:
        """Delete all settings for a user."""
        await self._ensure_schema()
        if settings.use_postgres:
            from core.memory import get_memory
            pool = await get_memory()._get_pg_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM user_settings WHERE user_id = $1", user_id)
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.db_path) as conn:
                await conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
                await conn.commit()

    async def get_all(self) -> list[dict]:
        """Return all user settings — for Admin Dashboard."""
        await self._ensure_schema()
        if settings.use_postgres:
            from core.memory import get_memory
            pool = await get_memory()._get_pg_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM user_settings ORDER BY updated_at DESC")
            return [dict(r) for r in rows]
        else:
            import aiosqlite
            async with aiosqlite.connect(settings.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute("SELECT * FROM user_settings ORDER BY updated_at DESC")
                rows = await cursor.fetchall()
            return [dict(r) for r in rows]


_store: Optional[UserSettingsStore] = None

def get_user_settings() -> UserSettingsStore:
    global _store
    if _store is None:
        _store = UserSettingsStore()
    return _store
