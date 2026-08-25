"""
Ujjwal Brain — Persistent Owner Profiler
══════════════════════════════════════════
Builds and maintains a deep, persistent profile of Ujjwal Kumar
(the bot owner) from every message he sends.

Architecture:
  - Runs as a background async task after every owner message
  - Extracts and stores facts, preferences, projects, tech stack
  - Profile stored in data/user_brain.json — survives all restarts
  - Injected as [OWNER CONTEXT] block into every AI prompt
  - Grows smarter over time — the more you chat, the richer the context

Owner recognition:
  - Discord: username/display_name contains 'ujjwal'
  - Telegram: handled via separate check
  - Owner ID: stored in OWNER_DISCORD_ID env for strict matching
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)

_BRAIN_PATH = Path("data/user_brain.json")

# Default seed profile — what we know about Ujjwal upfront
_DEFAULT_PROFILE: dict = {
    "name": "Ujjwal Kumar",
    "role": "Owner, Creator, and Administrator of OmniAgent",
    "known_facts": [
        "Runs OmniAgent on a personal homelab server",
        "Uses Ubuntu Linux as the host OS",
        "Runs Docker for containerization",
        "Has Ollama installed locally for offline AI inference",
        "Interested in AI, homelab, automation, and software development",
    ],
    "preferences": {
        "response_style": "concise and professional",
        "code_style": "clean, well-commented Python",
        "language": "English or Hindi depending on the message",
    },
    "current_projects": ["OmniAgent (this bot)"],
    "tech_stack": ["Python", "Docker", "Discord.py", "LangGraph", "Ollama", "SQLite"],
    "last_seen": None,
    "total_messages": 0,
    "conversation_topics": [],
    "updated_at": None,
}

# Keywords to auto-detect project/tech mentions
_TECH_KEYWORDS = [
    "python", "javascript", "typescript", "rust", "go", "java", "docker",
    "kubernetes", "k8s", "react", "fastapi", "flask", "django", "postgres",
    "mysql", "redis", "mongodb", "ollama", "langchain", "langgraph", "discord",
    "telegram", "aws", "gcp", "azure", "nginx", "linux", "ubuntu", "debian",
    "raspberry pi", "homelab", "git", "github", "llm", "openai", "gemini",
]


class UjjwalBrain:
    """
    Persistent owner profiler. Loads profile from disk on init,
    updates it after every owner message, saves back to disk.
    """

    def __init__(self) -> None:
        self._profile: dict = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def _load(self) -> None:
        """Load profile from disk, seeding with defaults if not found."""
        if self._loaded:
            return
        _BRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _BRAIN_PATH.exists():
            try:
                self._profile = json.loads(_BRAIN_PATH.read_text())
                log.info("UjjwalBrain loaded from %s (%d facts)",
                         _BRAIN_PATH, len(self._profile.get("known_facts", [])))
            except Exception as exc:
                log.warning("UjjwalBrain load error: %s — using defaults", exc)
                self._profile = dict(_DEFAULT_PROFILE)
        else:
            self._profile = dict(_DEFAULT_PROFILE)
            log.info("UjjwalBrain: no profile found, seeding defaults")
        self._loaded = True

    async def _save(self) -> None:
        """Persist current profile to disk atomically."""
        try:
            tmp = _BRAIN_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._profile, indent=2, ensure_ascii=False))
            tmp.replace(_BRAIN_PATH)
        except Exception as exc:
            log.error("UjjwalBrain save failed: %s", exc)

    async def process_message(self, message: str, platform: str = "discord") -> None:
        """
        Background-safe: extract info from an owner message and update the profile.
        Call this as a fire-and-forget task after every owner message.
        """
        await self._load()
        async with self._lock:
            msg_lower = message.lower()

            # Track message count + last seen
            self._profile["total_messages"] = self._profile.get("total_messages", 0) + 1
            self._profile["last_seen"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
            self._profile["updated_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

            # Auto-detect tech stack mentions
            tech_stack: list = self._profile.setdefault("tech_stack", [])
            for tech in _TECH_KEYWORDS:
                if tech in msg_lower and tech.title() not in tech_stack and tech not in tech_stack:
                    tech_stack.append(tech.title() if len(tech) > 3 else tech.upper())

            # Auto-detect project mentions ("I'm building X", "working on X", etc.)
            project_patterns = [
                "working on ", "building ", "created ", "made ", "developing ",
                "my project ", "my app ", "my bot ", "new feature",
            ]
            projects: list = self._profile.setdefault("current_projects", [])
            for pattern in project_patterns:
                if pattern in msg_lower:
                    # Extract the snippet after the pattern
                    idx = msg_lower.find(pattern)
                    snippet = message[idx + len(pattern):idx + len(pattern) + 40].split("\n")[0].strip().strip(".,!?")
                    if snippet and len(snippet) > 3 and snippet not in projects:
                        projects.append(snippet)
                        # Keep max 10 projects
                        if len(projects) > 10:
                            projects.pop(0)

            # Track conversation topics (last 20 unique keywords)
            topics: list = self._profile.setdefault("conversation_topics", [])
            topic_keywords = [w for w in msg_lower.split() if len(w) > 5 and w.isalpha()]
            for kw in topic_keywords[:3]:
                if kw not in topics:
                    topics.append(kw)
            if len(topics) > 20:
                self._profile["conversation_topics"] = topics[-20:]

            await self._save()

    async def add_fact(self, fact: str) -> None:
        """Manually add a persistent fact about Ujjwal (called by !remember command)."""
        await self._load()
        async with self._lock:
            facts: list = self._profile.setdefault("known_facts", [])
            if fact not in facts:
                facts.append(fact)
                await self._save()
                log.info("UjjwalBrain: added fact: %s", fact)

    async def build_context_block(self) -> str:
        """
        Build the [OWNER CONTEXT] block injected into every AI prompt
        when Ujjwal is the user.
        """
        await self._load()
        p = self._profile

        facts = p.get("known_facts", [])[:6]  # Top 6 facts
        projects = p.get("current_projects", [])[:4]
        tech = p.get("tech_stack", [])[:8]
        prefs = p.get("preferences", {})

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"[OWNER PROFILE: {p.get('name', 'Ujjwal Kumar')}]",
            f"Role: {p.get('role', 'Owner & Creator')}",
        ]
        if facts:
            lines.append("Known Facts:")
            for f in facts:
                lines.append(f"  • {f}")
        if projects:
            lines.append(f"Current Projects: {', '.join(projects)}")
        if tech:
            lines.append(f"Tech Stack: {', '.join(tech)}")
        if prefs:
            style = prefs.get('response_style', '')
            if style:
                lines.append(f"Preferred Style: {style}")
        msgs = p.get('total_messages', 0)
        lines.append(f"Total interactions: {msgs}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def get_profile(self) -> dict:
        """Return the raw profile dict."""
        await self._load()
        return dict(self._profile)


# Module-level singleton
_brain: Optional[UjjwalBrain] = None

def get_brain() -> UjjwalBrain:
    """Return the global UjjwalBrain singleton."""
    global _brain
    if _brain is None:
        _brain = UjjwalBrain()
    return _brain


def is_owner(username: str, display_name: str = "") -> bool:
    """
    Check if a Discord/Telegram user is Ujjwal (the owner).
    Returns True if username or display_name contains 'ujjwal'.
    """
    combined = (username + " " + display_name).lower()
    return "ujjwal" in combined
