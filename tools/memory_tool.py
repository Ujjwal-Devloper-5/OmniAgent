"""
Session-Isolated Memory Tools — OmniAgent Phase 4
════════════════════════════════════════════════════
Persistent note storage per session using UnifiedMemory backend.
Notes are completely isolated per session_id — no cross-user leakage.
"""
from __future__ import annotations

import json
from langchain_core.tools import tool
from core.logger import get_logger

log = get_logger(__name__)

# Memory key prefix for notes stored in UnifiedMemory
_NOTE_KEY_PREFIX = "__note__"


@tool
async def remember_note(
    note: str,
    tag: str = "general",
    session_id: str = "default",
) -> str:
    """
    Save a note to persistent session memory for future reference.
    
    Notes persist across conversation turns within the same session.
    Each note is tagged for easy retrieval.
    
    Args:
        note:       The note content to remember
        tag:        Category tag for organization (e.g. 'todo', 'fact', 'code')
        session_id: Session identifier (notes are isolated per session)
    
    Returns:
        Confirmation that the note was saved.
    
    Examples:
        remember_note('User prefers Python over JavaScript', tag='preference')
        remember_note('API key is abc123', tag='credential')  # use carefully
        remember_note('TODO: refactor the login module', tag='todo')
    """
    from core.memory import get_memory
    
    mem = get_memory()
    # Store as a special system turn in memory with a note marker
    note_data = json.dumps({"tag": tag, "content": note})
    await mem.add_turn(
        session_id,
        "system",
        f"{_NOTE_KEY_PREFIX}{note_data}",
    )
    log.info("Note saved | session=%s tag=%s", session_id, tag)
    return f"✅ Note saved (tag: {tag}): {note[:100]}"


@tool
async def recall_notes(
    tag: str = "",
    session_id: str = "default",
    limit: int = 20,
) -> str:
    """
    Retrieve saved notes from session memory.
    
    Args:
        tag:        Filter by tag (empty = return all notes)
        session_id: Session identifier
        limit:      Maximum number of notes to return (default: 20)
    
    Returns:
        Formatted list of matching notes with their tags.
    
    Examples:
        recall_notes()              # all notes
        recall_notes(tag='todo')    # only TODO items
        recall_notes(tag='fact', limit=5)  # last 5 facts
    """
    from core.memory import get_memory
    
    mem = get_memory()
    history = await mem.get_history(session_id)
    
    notes: list[dict] = []
    for turn in history:
        content = turn.get("content", "")
        if content.startswith(_NOTE_KEY_PREFIX):
            try:
                data = json.loads(content[len(_NOTE_KEY_PREFIX):])
                if data.get("tag") == "__forgotten__":
                    continue  # Skip forgotten notes
                if not tag or data.get("tag") == tag:
                    notes.append(data)
            except (json.JSONDecodeError, KeyError):
                pass
    
    # Return most recent first, capped at limit
    notes = notes[-limit:]
    notes.reverse()
    
    if not notes:
        return f"📝 No notes found{' with tag: ' + tag if tag else ''}."
    
    lines = [f"📝 Found {len(notes)} note(s){' tagged ' + tag if tag else ''}:"]
    for i, n in enumerate(notes, 1):
        lines.append(f"  {i}. [{n['tag']}] {n['content']}")
    return "\n".join(lines)


@tool
async def forget_note(
    keyword: str,
    session_id: str = "default",
) -> str:
    """
    Mark a note for forgetting by keyword match.
    
    Because notes are stored in conversation history (immutable in many backends),
    this adds a 'forget' marker for the matched note so recall_notes filters it out.
    
    Args:
        keyword:    Text that must appear in the note content to forget it
        session_id: Session identifier
    
    Returns:
        Confirmation of how many notes were forgotten.
    """
    from core.memory import get_memory
    
    mem = get_memory()
    history = await mem.get_history(session_id)
    
    forgotten = 0
    for turn in history:
        content = turn.get("content", "")
        if content.startswith(_NOTE_KEY_PREFIX):
            try:
                data = json.loads(content[len(_NOTE_KEY_PREFIX):])
                if keyword.lower() in data.get("content", "").lower():
                    # Add a forget marker
                    await mem.add_turn(
                        session_id,
                        "system",
                        f"{_NOTE_KEY_PREFIX}{json.dumps({'tag': '__forgotten__', 'content': data['content']})}",
                    )
                    forgotten += 1
            except (json.JSONDecodeError, KeyError):
                pass
    
    if forgotten:
        return f"🗑️ Forgot {forgotten} note(s) containing '{keyword}'."
    return f"❌ No notes found containing '{keyword}'."
