"""
Memory Tools — OmniAgent v4
══════════════════════════════
Tools for the AI agent to save and recall persistent notes and facts.
Notes survive restarts and are stored in the SQLite database.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)

_NOTES_PATH = Path("data/notes.json")


def _load_notes() -> dict:
    """Load notes from disk."""
    _NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _NOTES_PATH.exists():
        try:
            return json.loads(_NOTES_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_notes(notes: dict) -> None:
    """Save notes to disk atomically."""
    tmp = _NOTES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, indent=2, ensure_ascii=False))
    tmp.replace(_NOTES_PATH)


@tool
def remember_note(key: str, value: str) -> str:
    """
    Save a persistent note or fact with a key. Notes survive bot restarts.
    Use this to remember important information the user tells you to save.
    Examples: remember_note('favorite_color', 'blue'), remember_note('server_ip', '192.168.1.100')
    """
    try:
        notes = _load_notes()
        notes[key] = {
            "value": value,
            "saved_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        _save_notes(notes)
        return f"✅ Remembered: '{key}' = '{value}'"
    except Exception as e:
        return f"Error saving note: {e}"


@tool
def recall_notes(key: Optional[str] = None) -> str:
    """
    Recall saved notes. If a key is provided, returns that specific note.
    If no key is provided, returns ALL saved notes.
    Use this when the user asks 'what do you remember about X?' or 'what did I tell you?'
    """
    try:
        notes = _load_notes()
        if not notes:
            return "No notes saved yet."
        if key:
            if key in notes:
                n = notes[key]
                return f"📝 Note '{key}': {n['value']} (saved {n.get('saved_at', 'unknown')})"
            else:
                # Try fuzzy match
                matches = [k for k in notes if key.lower() in k.lower()]
                if matches:
                    lines = [f"No exact match for '{key}', similar notes:"]
                    for m in matches[:5]:
                        lines.append(f"  • {m}: {notes[m]['value']}")
                    return "\n".join(lines)
                return f"No note found for key '{key}'."
        else:
            lines = [f"📝 All saved notes ({len(notes)} total):"]
            for k, v in list(notes.items())[-20:]:  # Last 20
                lines.append(f"  • {k}: {v['value']}")
            return "\n".join(lines)
    except Exception as e:
        return f"Error recalling notes: {e}"
