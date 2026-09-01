"""
Dynamic Model Registry — OmniAgent Phase 6
═══════════════════════════════════════════
Replaces hardcoded _TASK_PREFERENCES with a live scored model pool.

How it works:
1. At boot, loads models.json to build the full model catalogue
2. Filters to only AVAILABLE models (provider key configured + model responds)
3. For each request, scores all eligible models and picks the highest scorer
4. Scoring formula:
     base  = intelligence * 3.0 + speed * 1.0
     if needs_tools: base += tool_reliability * 2.0
     if needs_vision: hard +5 bonus for vision models, -100 for blind models
     health_penalty = min(100, consecutive_failures * 20)
     final_score = base - health_penalty
5. Health tracking: consecutive failures demote a model's effective score

Config file: models.json (project root — JSON because PyYAML not installed)
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Path to registry config (project root, alongside main.py / config.py)
# ─────────────────────────────────────────────────────────────────────────────
_REGISTRY_PATH = Path(__file__).parent.parent / "models.json"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelEntry:
    """A single model entry in the registry with capability scores."""

    id: str                       # e.g. "gemini-2.5-flash"
    provider: str                 # e.g. "gemini"
    intelligence: int             # 1-10: raw reasoning quality
    speed: int                    # 1-10: relative response speed
    tool_reliability: int         # 1-10: KEY — small local models score low here
    vision: bool                  # True if model can process images
    context_window: int           # token context limit
    tags: list[str]               # e.g. ["coding", "math", "general"]
    is_available: bool = False    # set at boot after provider availability check
    consecutive_failures: int = 0  # runtime health — incremented on each failure

    def compute_score(
        self,
        needs_tools: bool = False,
        needs_vision: bool = False,
    ) -> float:
        """
        Compute the routing score for this model given request requirements.

        Higher is better.  Returns a very negative number if vision is
        required and this model is blind (hard exclusion via scoring).
        """
        base: float = self.intelligence * 3.0 + self.speed * 1.0

        if needs_tools:
            base += self.tool_reliability * 2.0

        if needs_vision:
            # Hard bonus/penalty: vision models win decisively, blind models lose
            base += 5.0 if self.vision else -100.0

        # Health penalty: each consecutive failure costs 20 points, capped at 100
        health_penalty = min(100, self.consecutive_failures * 20)
        return base - health_penalty


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

class ModelRegistry:
    """
    Dynamic model registry loaded from models.json.

    Lifecycle
    ---------
    1. __init__: Parse models.json → build _models dict (all models, not yet
       marked available).
    2. initialize(available_providers): Mark models as available based on
       which provider API keys are actually configured.
    3. add_ollama_models(installed): Auto-register any Ollama models detected
       at boot that aren't already catalogued (safe default low scores).
    4. Per-request: select_model / get_ranked_list → scored routing.
    5. record_success / record_failure → runtime health tracking.
    """

    def __init__(self) -> None:
        # model_id → ModelEntry
        self._models: dict[str, ModelEntry] = {}
        # Protects concurrent health updates (sync, not async — just counters)
        self._lock = threading.Lock()
        self._load_registry()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_registry(self) -> None:
        """Parse models.json and populate _models.  Skips bad entries."""
        if not _REGISTRY_PATH.exists():
            log.warning(
                "Model registry not found at %s — registry will be empty",
                _REGISTRY_PATH,
            )
            return

        try:
            raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error("Failed to parse %s: %s", _REGISTRY_PATH, exc)
            return

        models_data = raw.get("models", [])
        loaded = 0
        for entry in models_data:
            try:
                model = ModelEntry(
                    id=entry["id"],
                    provider=entry["provider"].lower(),
                    intelligence=int(entry["intelligence"]),
                    speed=int(entry["speed"]),
                    tool_reliability=int(entry["tool_reliability"]),
                    vision=bool(entry["vision"]),
                    context_window=int(entry["context_window"]),
                    tags=[t.lower() for t in entry.get("tags", [])],
                )
                self._models[model.id] = model
                loaded += 1
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping malformed registry entry %s: %s", entry, exc)

        log.info(
            "ModelRegistry loaded %d models from %s",
            loaded,
            _REGISTRY_PATH.name,
        )

    # ── Boot-time initialisation ───────────────────────────────────────────────

    async def initialize(self, available_providers: set[str]) -> None:
        """
        Mark models as available based on which providers have API keys.

        Parameters
        ----------
        available_providers : set of provider name strings, e.g. {"gemini", "ollama"}
        """
        with self._lock:
            marked = 0
            for model in self._models.values():
                was_available = model.is_available
                model.is_available = model.provider in available_providers
                if model.is_available and not was_available:
                    marked += 1

        log.info(
            "ModelRegistry.initialize | available_providers=%s | marked_available=%d / %d total",
            sorted(available_providers),
            marked,
            len(self._models),
        )

    async def add_ollama_models(self, installed_models: list[str]) -> None:
        """
        Auto-register Ollama models discovered at boot that are not in models.json.

        Unregistered models receive conservative default scores so the
        registry can still use them as a last resort without over-trusting them.

        Parameters
        ----------
        installed_models : list of model name strings from Ollama /api/tags
        """
        with self._lock:
            new_count = 0
            for model_name in installed_models:
                # Normalise: strip tag if present e.g. "llama3.2:latest" → base kept
                if model_name not in self._models:
                    # Guess vision capability by name substring
                    name_lower = model_name.lower()
                    is_vision = any(
                        sub in name_lower
                        for sub in ("vl", "llava", "bakllava", "minicpm-v", "gemma3")
                    )
                    entry = ModelEntry(
                        id=model_name,
                        provider="ollama",
                        intelligence=4,     # conservative unknown model
                        speed=7,
                        tool_reliability=2, # unknown tool support — assume poor
                        vision=is_vision,
                        context_window=32000,
                        tags=["general", "quick"] + (["vision"] if is_vision else []),
                        is_available=True,  # it's installed, so it IS available
                    )
                    self._models[model_name] = entry
                    new_count += 1
                    log.debug(
                        "Auto-registered unknown Ollama model '%s' (vision=%s)",
                        model_name,
                        is_vision,
                    )
                else:
                    # Already in registry — just mark it as available
                    self._models[model_name].is_available = True

            if new_count:
                log.info(
                    "ModelRegistry auto-registered %d previously unknown Ollama model(s)",
                    new_count,
                )

    # ── Scoring & Selection ───────────────────────────────────────────────────

    def _eligible_models(
        self,
        task_type: str,
        needs_vision: bool,
    ) -> list[ModelEntry]:
        """
        Return the subset of available models eligible for this task.

        Eligibility rules:
        - is_available must be True
        - tags must include task_type OR "general"
        - If needs_vision is True, ONLY vision=True models qualify
          (vision penalty in scoring handles soft degradation; here we
          do a hard filter so blind models don't even appear in the pool
          when vision is strictly required).
        """
        result: list[ModelEntry] = []
        task_lower = task_type.lower()

        for model in self._models.values():
            if not model.is_available:
                continue

            # Tag match: task tag or general fallback
            if task_lower not in model.tags and "general" not in model.tags:
                continue

            # Hard vision filter
            if needs_vision and not model.vision:
                continue

            result.append(model)

        return result

    def select_model(
        self,
        task_type: str,
        needs_vision: bool = False,
        needs_tools: bool = False,
    ) -> Optional[ModelEntry]:
        """
        Return the single best-scoring available model for this request.

        Returns None if no eligible model is found (caller should fall back
        to the legacy _TASK_PREFERENCES list in model_router.py).
        """
        candidates = self._eligible_models(task_type, needs_vision)
        if not candidates:
            log.debug(
                "ModelRegistry.select_model: no candidates for task=%s vision=%s",
                task_type, needs_vision,
            )
            return None

        best = max(
            candidates,
            key=lambda m: m.compute_score(
                needs_tools=needs_tools,
                needs_vision=needs_vision,
            ),
        )
        log.debug(
            "ModelRegistry.select_model | task=%s vision=%s tools=%s → %s (score=%.1f)",
            task_type, needs_vision, needs_tools,
            best.id,
            best.compute_score(needs_tools=needs_tools, needs_vision=needs_vision),
        )
        return best

    def get_ranked_list(
        self,
        task_type: str,
        needs_vision: bool = False,
        needs_tools: bool = False,
    ) -> list[ModelEntry]:
        """
        Return ALL eligible models sorted by score descending.

        Used by model_router.py to build the full fallback chain.
        Returns an empty list if no eligible models exist.
        """
        candidates = self._eligible_models(task_type, needs_vision)
        return sorted(
            candidates,
            key=lambda m: m.compute_score(
                needs_tools=needs_tools,
                needs_vision=needs_vision,
            ),
            reverse=True,
        )

    # ── Health tracking ───────────────────────────────────────────────────────

    def record_success(self, model_id: str) -> None:
        """Reset consecutive_failures counter to 0 after a successful call."""
        with self._lock:
            model = self._models.get(model_id)
            if model and model.consecutive_failures > 0:
                log.debug("ModelRegistry: %s recovered (failures reset)", model_id)
                model.consecutive_failures = 0

    def record_failure(self, model_id: str) -> None:
        """Increment consecutive_failures counter, demoting the model's score."""
        with self._lock:
            model = self._models.get(model_id)
            if model:
                model.consecutive_failures += 1
                log.warning(
                    "ModelRegistry: %s failure #%d (score demotion -%d)",
                    model_id,
                    model.consecutive_failures,
                    min(100, model.consecutive_failures * 20),
                )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_registry_summary(self) -> str:
        """
        Return a compact human-readable summary of the live model pool.

        Used by the /status Discord command to show the scored model pool.
        Format: one line per available model, sorted by provider then score.
        """
        available = [m for m in self._models.values() if m.is_available]
        if not available:
            return "No models currently available."

        # Sort: by provider, then by general score descending
        available.sort(
            key=lambda m: (
                m.provider,
                -m.compute_score(needs_tools=True, needs_vision=False),
            )
        )

        lines: list[str] = []
        current_provider = ""
        for m in available:
            if m.provider != current_provider:
                current_provider = m.provider
                lines.append(f"**{m.provider.upper()}**")

            score = m.compute_score(needs_tools=True, needs_vision=False)
            vision_tag = " 👁️" if m.vision else ""
            health_tag = f" ⚠️×{m.consecutive_failures}" if m.consecutive_failures else ""
            lines.append(
                f"  `{m.id}`  score={score:.0f}{vision_tag}{health_tag}"
            )

        return "\n".join(lines)

    @property
    def total_models(self) -> int:
        """Total number of models in the catalogue (available or not)."""
        return len(self._models)

    @property
    def available_count(self) -> int:
        """Number of models currently marked as available."""
        return sum(1 for m in self._models.values() if m.is_available)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_registry: Optional[ModelRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ModelRegistry:
    """Return the global ModelRegistry singleton (lazy-initialised)."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ModelRegistry()
    return _registry
