"""
Ollama local model agent — smart per-task model selection.

Auto-discovers installed models at startup and picks the best one per task:
  • VISION/MEDIA  → qwen2.5vl (multimodal)
  • CODING        → qwen2.5-coder
  • REASONING     → deepseek-r1
  • GENERAL/QUICK → qwen3, qwen2.5, llama3.2
  • TINY fallback → tinyllama (fastest, always there)

Runs fully offline — perfect when all cloud providers are down.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from core.agents.base import build_system_prompt, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)

# ── Capability classification of known model families ──────────────────────────
# Each entry: (substring_in_model_name, capability_set)
# capability_set may include "tools" — means model supports function/tool calling API
_MODEL_CAPABILITIES: list[tuple[str, set[str]]] = [
    ("qwen2.5vl",      {"vision", "multimodal", "general", "quick"}),
    ("llava",          {"vision", "multimodal", "general"}),
    ("bakllava",       {"vision", "multimodal", "general"}),
    ("minicpm-v",      {"vision", "multimodal", "general"}),
    # Qwen family: tool calling works well
    ("qwen2.5-coder",  {"coding", "general", "quick", "math", "tools"}),
    ("qwen2.5",        {"coding", "general", "quick", "math", "tools"}),
    ("qwen3",          {"general", "quick", "math", "creative", "research", "tools"}),
    # DeepSeek: tools work on R1 distills via Ollama
    ("deepseek-r1",    {"math", "research", "analysis", "coding", "general", "tools"}),
    ("deepseek-coder", {"coding", "general", "tools"}),
    # Llama/Mistral/Phi/Gemma: basic chat, no reliable tool calling
    ("llama3.1",       {"general", "quick", "creative", "research", "tools"}),
    ("llama3.2",       {"general", "quick", "creative", "research"}),
    ("llama3",         {"general", "quick", "creative", "research"}),
    ("mistral",        {"general", "creative", "coding"}),
    ("phi",            {"general", "quick", "coding"}),
    ("gemma3",         {"vision", "multimodal", "general", "quick"}),
    ("gemma",          {"general", "quick"}),
    ("tinyllama",      {"quick", "general"}),
]

# Task → preferred capability keywords (ordered)
_TASK_CAPABILITY_PRIORITY: dict[str, list[str]] = {
    TaskType.CODING.value:   ["coding", "general"],
    TaskType.MATH.value:     ["math", "analysis", "general"],
    TaskType.RESEARCH.value: ["research", "analysis", "general"],
    TaskType.CREATIVE.value: ["creative", "general"],
    TaskType.ANALYSIS.value: ["analysis", "math", "general"],
    TaskType.QUICK.value:    ["quick", "general"],
    TaskType.GENERAL.value:  ["general", "quick"],
    "vision":                ["vision", "multimodal"],
}

# Models that support tool/function calling (substring match)
_TOOL_CAPABLE_SUBSTRINGS = ["qwen2.5", "qwen3", "deepseek-r1", "deepseek-coder", "llama3.1"]



class OllamaAgent(BaseAgent):
    """
    Smart local Ollama agent.

    Discovers installed models once at startup, then routes each request
    to the locally-installed model best suited for that task type.
    Falls back through alternatives and uses tinyllama as last resort.
    """

    provider = ModelProvider.OLLAMA

    def __init__(self) -> None:
        self._available_models: list[dict] = []      # Raw API response
        self._model_capabilities: dict[str, set[str]] = {}  # name → caps
        self._reachable: bool = False
        self._initialised: bool = False
        self._init_lock = asyncio.Lock()

    # ── Initialisation ─────────────────────────────────────────────────────────

    async def _ensure_initialised(self) -> None:
        """Lazy init: discover models once, thread-safe."""
        if self._initialised:
            return
        async with self._init_lock:
            if self._initialised:
                return
            await self._discover_models()
            self._initialised = True

    async def _discover_models(self) -> None:
        """Fetch installed models from Ollama and build capability map."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                if resp.status_code != 200:
                    self._reachable = False
                    log.warning("Ollama returned HTTP %d", resp.status_code)
                    return
                data = resp.json()
        except Exception as exc:
            self._reachable = False
            log.info("Ollama not reachable: %s", exc)
            return

        self._reachable = True
        self._available_models = data.get("models", [])
        self._model_capabilities = {}

        for model in self._available_models:
            name = model["name"]
            caps: set[str] = set()
            name_lower = name.lower()
            for substr, model_caps in _MODEL_CAPABILITIES:
                if substr in name_lower:
                    caps |= model_caps
            # If nothing matched, treat as general
            if not caps:
                caps = {"general"}
            self._model_capabilities[name] = caps

        model_summary = {
            name: sorted(caps)
            for name, caps in self._model_capabilities.items()
        }
        log.info(
            "Ollama discovered %d models: %s",
            len(self._available_models),
            model_summary,
        )

    # ── Availability ───────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        await self._ensure_initialised()
        return self._reachable and bool(self._available_models)

    def get_vision_model(self) -> Optional[str]:
        """Return best multimodal/vision model if installed, else None."""
        for name, caps in self._model_capabilities.items():
            if "vision" in caps or "multimodal" in caps:
                return name
        return None

    def has_vision_capability(self) -> bool:
        return self.get_vision_model() is not None

    # ── Model selection ────────────────────────────────────────────────────────

    def pick_model(self, task_type: TaskType, needs_vision: bool = False) -> str:
        """
        Select the best locally-installed Ollama model for this task.

        Priority logic:
        1. If vision needed → vision-capable model
        2. Match task-specific capability
        3. Prefer larger parameter counts among ties
        4. Hard fallback: first available model, then settings.ollama_model
        """
        if not self._model_capabilities:
            return settings.ollama_model

        cap_priority = (
            _TASK_CAPABILITY_PRIORITY["vision"]
            if needs_vision
            else _TASK_CAPABILITY_PRIORITY.get(task_type.value, ["general"])
        )

        # Score each installed model
        scored: list[tuple[float, str]] = []
        for name, caps in self._model_capabilities.items():
            model_info = next(
                (m for m in self._available_models if m["name"] == name), {}
            )
            # Capability score: position in priority list (higher = better)
            cap_score = 0
            for i, wanted_cap in enumerate(cap_priority):
                if wanted_cap in caps:
                    cap_score = len(cap_priority) - i
                    break

            # Size score: prefer larger models (parse parameter size)
            size_score = self._parse_param_size(
                model_info.get("details", {}).get("parameter_size", "0B")
            )

            scored.append((cap_score * 1000 + size_score, name))

        if not scored:
            return settings.ollama_model

        scored.sort(reverse=True)
        chosen = scored[0][1]
        log.debug(
            "Ollama model pick | task=%s vision=%s → %s (scores=%s)",
            task_type.value, needs_vision,
            chosen, [(s, n) for s, n in scored[:3]],
        )
        return chosen

    @staticmethod
    def _parse_param_size(size_str: str) -> float:
        """Parse '7.6B' → 7.6, '3.8B' → 3.8, etc."""
        try:
            s = size_str.upper().replace("B", "").strip()
            return float(s)
        except Exception:
            return 0.0

    def _model_supports_tools(self, model_name: str) -> bool:
        """Return True if this model reliably supports tool/function calling."""
        name_lower = model_name.lower()
        # Check capability map first
        caps = self._model_capabilities.get(model_name, set())
        if "tools" in caps:
            return True
        # Fallback: substring check
        return any(s in name_lower for s in _TOOL_CAPABLE_SUBSTRINGS)

    # ── Message processing ─────────────────────────────────────────────────────

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 2,
        platform_system_note: str = "",
        needs_vision: bool = False,
        image_data: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> AgentResponse:
        await self._ensure_initialised()

        model_name = self.pick_model(task_type, needs_vision=needs_vision)

        log.info(
            "Ollama | task=%s vision=%s model=%s session=%s",
            task_type.value, needs_vision, model_name, session_id,
        )

        # ── Vision/multimodal tasks: raw Ollama API (no tools — model doesn't support them)
        if needs_vision:
            return await self._call_vision_direct(
                model_name, message, session_id, task_type, platform_system_note, max_retries
            )

        # ── Text tasks: use react agent only for tool-capable models
        if self._model_supports_tools(model_name):
            return await self._call_with_tools(
                model_name, message, session_id, task_type, platform_system_note, max_retries
            )
        else:
            log.info("Ollama | model=%s lacks tool support — using direct chat", model_name)
            return await self._call_direct_chat(
                model_name, message, session_id, task_type, platform_system_note, max_retries
            )

    async def _call_with_tools(
        self,
        model_name: str,
        message: str,
        session_id: str,
        task_type: TaskType,
        platform_system_note: str,
        max_retries: int,
    ) -> AgentResponse:
        """Use LangGraph react agent with full tool support (qwen3, qwen2.5, etc.)."""
        from langchain_ollama import ChatOllama
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.prebuilt import create_react_agent
        from tools.registry import get_tools

        llm = ChatOllama(
            model=model_name,
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout,
        )
        tools = get_tools()

        async def _call() -> AgentResponse:
            effective_prompt = await build_system_prompt(platform_system_note, session_id)
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": session_id}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from Ollama")
                return AgentResponse(
                    content=content,
                    provider=self.provider,
                    model_name=model_name,
                    session_id=session_id,
                    task_type=task_type,
                    tokens_used=len(content) // 4,
                )

        return await self._retry(_call, session_id, max_retries)

    async def _call_direct_chat(
        self,
        model_name: str,
        message: str,
        session_id: str,
        task_type: TaskType,
        platform_system_note: str,
        max_retries: int,
    ) -> AgentResponse:
        """Direct Ollama /api/chat call for models that don't support tool calling."""
        system = await build_system_prompt(platform_system_note, session_id)

        payload = {
            "model": model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": message},
            ],
        }

        async def _call() -> AgentResponse:
            async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "").strip()
                if not content:
                    raise ValueError("Empty response from Ollama direct chat")
                return AgentResponse(
                    content=content,
                    provider=self.provider,
                    model_name=model_name,
                    session_id=session_id,
                    task_type=task_type,
                    tokens_used=len(content) // 4,
                )

        return await self._retry(_call, session_id, max_retries)



    async def _call_vision_direct(
        self,
        model_name: str,
        message: str,
        session_id: str,
        task_type: TaskType,
        platform_system_note: str,
        max_retries: int,
    ) -> AgentResponse:
        """
        Call Ollama directly via raw HTTP for vision/multimodal models.
        These models do not support the tool-calling protocol.
        Conversation history is maintained manually in memory (no SQLite for vision).
        """
        system = (
            "You are OmniAgent, a helpful AI assistant created by Ujjwal Kumar. "
            "You can analyse images and media described to you. "
            "Be concise, accurate, and helpful. Format responses in markdown."
            + (f"\n\n{platform_system_note}" if platform_system_note else "")
        )

        payload = {
            "model": model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": message},
            ],
        }

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
                    resp = await client.post(
                        f"{settings.ollama_base_url}/api/chat",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                content = data.get("message", {}).get("content", "").strip()
                if not content:
                    raise ValueError("Empty vision response from Ollama")

                log.info(
                    "Ollama vision direct | model=%s tokens≈%d",
                    model_name, len(content) // 4,
                )
                return AgentResponse(
                    content=content,
                    provider=self.provider,
                    model_name=model_name,
                    session_id=session_id,
                    task_type=task_type,
                    tokens_used=len(content) // 4,
                )
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Ollama vision attempt %d/%d failed: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        raise RuntimeError(
            f"ollama vision failed after {max_retries} retries: {last_error}"
        )

    async def clear_memory(self, session_id: str) -> None:
        await self._clear_sqlite_memory(session_id, "ollama")

    async def health_check(self) -> bool:
        # Re-discover on every health check so models refresh
        self._initialised = False
        await self._ensure_initialised()
        return self._reachable

