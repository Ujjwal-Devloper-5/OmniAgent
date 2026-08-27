"""
Context Window Guard — Smart Conversation Trimming
════════════════════════════════════════════════════

Problem: LangGraph stores ALL messages in its SQLite checkpoint forever.
After 50+ turns, the model's context window fills up, causing:
  - Token limit errors
  - Degraded response quality (model loses track of early context)
  - Increased latency (more tokens = slower inference)

Solution:
  Before each agent call, count how many messages are stored for this session.
  If over the threshold:
    1. Load the last MAX_RECENT_TURNS messages verbatim (full fidelity)
    2. Summarize OLDER turns into a compact "Conversation Summary" block
    3. Wipe the old checkpoint from SQLite
    4. Re-inject the summary as a system message + the recent turns
    5. Save the trimmed checkpoint back — session continues seamlessly

This is transparent to the user — they never see a gap in the conversation.
The AI sees: [summary of past] + [last N turns verbatim].

Design principles:
  - Only triggers when actually needed (lazy — zero overhead on short sessions)
  - Summary is created by a lightweight LLM call (uses the cheapest available provider)
  - Full conversation never lost — UnifiedMemory always has the raw log
  - Thread-safe (uses an asyncio.Lock per session)
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import aiosqlite

from core.logger import get_logger

log = get_logger(__name__)

# ─── Tuning constants ────────────────────────────────────────────────────────
# Trim when stored LangGraph checkpoint messages exceed this count
_TRIM_THRESHOLD   = 40   # messages (user + assistant turns combined)
# Keep this many recent messages verbatim (untouched)
_MAX_RECENT_TURNS = 20
# Lock map — one lock per session_id to prevent concurrent trims
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


async def _count_checkpoint_messages(session_id: str, db_path: str) -> int:
    """
    Count the total number of messages stored in LangGraph's checkpoint for this session.
    Returns 0 if no checkpoint exists or if the table doesn't exist.
    Checks both exact thread_id AND thread_id LIKE 'session_id%' to catch provider suffixes.
    """
    try:
        async with aiosqlite.connect(db_path) as conn:
            # Check if checkpoints table exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
            )
            if not await cursor.fetchone():
                return 0
            # Count checkpoint entries (each entry = one checkpoint state = many messages)
            # We approximate message count by counting writes (each write = one message)
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM writes WHERE thread_id LIKE ?",
                (f"{session_id}%",)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
    except Exception as exc:
        log.debug("Context count failed (non-fatal): %s", exc)
        return 0


async def _get_unified_history(session_id: str, db_path: str, max_turns: int) -> list[dict]:
    """Load the last N turns from UnifiedMemory (our reliable long-term store)."""
    try:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT role, content, provider, model, ts
                FROM unified_memory
                WHERE session_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (session_id, max_turns * 2),  # fetch double to have enough for both old+recent
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


async def _build_summary_via_llm(history_text: str) -> str:
    """
    Generate a concise summary of old conversation turns.
    Uses a minimal LLM call (no tools, no checkpointer) for efficiency.
    Falls back to a simple truncation if LLM is unavailable.
    """
    if not history_text.strip():
        return ""

    prompt = (
        "You are a conversation summarizer. Below is a transcript of an older portion "
        "of a conversation. Create a CONCISE summary (max 300 words) that captures:\n"
        "- Key facts learned about the user\n"
        "- Important decisions or conclusions reached\n"
        "- Any ongoing tasks or projects mentioned\n"
        "- User preferences and communication style\n\n"
        "Be factual and dense. Do not use filler words.\n\n"
        f"TRANSCRIPT:\n{history_text}\n\nSUMMARY:"
    )

    # Try to use the cheapest available provider for summarization
    try:
        from config import settings
        if settings.openrouter_api_key:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="liquid/lfm-2.5-2.6b:free",  # tiny, fast, free
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.2,
                max_tokens=400,
            )
            result = await llm.ainvoke(prompt)
            return result.content.strip()
    except Exception as exc:
        log.debug("Summary LLM failed, using truncation: %s", exc)

    # Fallback: return first 600 chars of the history as a manual summary
    return "[Earlier conversation truncated for context efficiency]\n" + history_text[:600]


async def _wipe_checkpoint(session_id: str, db_path: str) -> None:
    """Delete all LangGraph checkpoint data for this session (across all providers)."""
    try:
        async with aiosqlite.connect(db_path) as conn:
            tables = ["checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs"]
            # Get which ones actually exist
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            existing = {row[0] async for row in cursor}
            for table in tables:
                if table in existing:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE thread_id LIKE ?",
                        (f"{session_id}%",)
                    )
            await conn.commit()
    except Exception as exc:
        log.warning("Failed to wipe checkpoint for %s: %s", session_id, exc)


async def maybe_trim_context(
    session_id: str,
    db_path: str,
) -> Optional[str]:
    """
    Check if this session's context needs trimming. If so, trim it.

    Returns:
        str: A formatted context summary string to prepend to the next message,
             so the agent has the condensed history even after checkpoint wipe.
        None: No trimming needed — context is within limits.

    This is called by agents BEFORE invoking LangGraph, so the agent knows
    whether to inject a context summary into the user message.
    """
    lock = _get_session_lock(session_id)
    async with lock:
        msg_count = await _count_checkpoint_messages(session_id, db_path)
        if msg_count < _TRIM_THRESHOLD:
            return None  # Within limits — nothing to do

        log.info(
            "Context trim triggered | session=%s checkpoint_writes=%d (threshold=%d)",
            session_id, msg_count, _TRIM_THRESHOLD
        )
        t0 = time.monotonic()

        # Load full history from UnifiedMemory (our authoritative long-term store)
        all_history = await _get_unified_history(session_id, db_path, max_turns=100)
        if not all_history:
            # No history in UnifiedMemory — just wipe the checkpoint
            await _wipe_checkpoint(session_id, db_path)
            return None

        # Split into old (to summarize) and recent (to keep verbatim)
        if len(all_history) <= _MAX_RECENT_TURNS:
            # Not enough history to trim meaningfully
            return None

        old_turns = all_history[:-_MAX_RECENT_TURNS]
        recent_turns = all_history[-_MAX_RECENT_TURNS:]

        # Build text of old turns to summarize
        old_text_parts = []
        for turn in old_turns:
            role = "User" if turn["role"] == "user" else "Assistant"
            content = turn["content"]
            if len(content) > 500:
                content = content[:500] + "..."
            old_text_parts.append(f"{role}: {content}")
        old_text = "\n".join(old_text_parts)

        # Generate summary of old turns
        summary = await _build_summary_via_llm(old_text)

        # Wipe the old bloated checkpoint — the agent will start fresh with trimmed context
        await _wipe_checkpoint(session_id, db_path)

        elapsed = time.monotonic() - t0
        log.info(
            "Context trimmed | session=%s old_turns=%d recent=%d summary_len=%d elapsed=%.2fs",
            session_id, len(old_turns), len(recent_turns), len(summary), elapsed
        )

        # Build the context injection string to prepend to the next user message
        context_lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "[CONTEXT SUMMARY — Earlier Conversation]",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            summary,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "[RECENT CONVERSATION — Last 20 Turns]",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for turn in recent_turns:
            role = "User" if turn["role"] == "user" else "You"
            content = turn["content"]
            if len(content) > 400:
                content = content[:400] + "..."
            context_lines.append(f"{role}: {content}")
        context_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return "\n".join(context_lines)
