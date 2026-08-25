"""
Groq agent backend.

Groq offers FREE ultra-fast inference via their custom LPU hardware.
Excellent for quick responses and high throughput.

Free tier models (no credit card needed):
  - llama-3.3-70b-versatile   (70B, smart & fast)
  - llama-3.1-8b-instant      (8B, extremely fast)
  - mixtral-8x7b-32768        (MoE, great at code)
  - gemma2-9b-it              (Google Gemma 2)
  - llama-3.1-70b-versatile   (70B versatile)

Get a free API key at: https://console.groq.com/
"""

from __future__ import annotations

import asyncio

from core.agents.base import build_system_prompt, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)

# Best Groq model per task type
_GROQ_TASK_MODELS: dict[TaskType, str] = {
    TaskType.CODING:   "moonshotai/kimi-k2-instruct",
    TaskType.MATH:     "meta-llama/llama-4-maverick-17b-128e-instruct",
    TaskType.CREATIVE: "llama-3.3-70b-versatile",
    TaskType.RESEARCH: "llama-3.3-70b-versatile",
    TaskType.ANALYSIS: "llama-3.3-70b-versatile",
    TaskType.QUICK:    "llama-3.1-8b-instant",
    TaskType.GENERAL:  "llama-3.3-70b-versatile",
    TaskType.VISION:   "llama-3.1-8b-instant",   # text-only fallback for vision tasks
}


class GroqAgent(BaseAgent):
    """
    Groq agent — FREE ultra-fast inference via LPU hardware.
    Tokens per minute limits apply on the free tier.
    """

    provider = ModelProvider.GROQ

    def __init__(self) -> None:
        self._available = bool(settings.groq_api_key)

    async def is_available(self) -> bool:
        return self._available

    def _pick_model(self, task_type: TaskType) -> str:
        """Pick the best Groq model for the task."""
        # Allow override from config
        if settings.groq_model:
            return settings.groq_model
        return _GROQ_TASK_MODELS.get(task_type, "llama-3.3-70b-versatile")

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 3,
        platform_system_note: str = "",
        needs_vision: bool = False,
        image_data: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> AgentResponse:
        if not settings.groq_api_key:
            return AgentResponse(
                content="Groq is not configured.",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        try:
            from langchain_groq import ChatGroq
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.prebuilt import create_react_agent
            from tools.registry import get_tools
        except ImportError as exc:
            raise RuntimeError(f"Groq dependencies missing: {exc}")

        model_name = self._pick_model(task_type)
        llm = ChatGroq(
            model=model_name,
            groq_api_key=settings.groq_api_key,
            temperature=settings.groq_temperature,
            max_tokens=settings.groq_max_tokens,
        )
        tools = get_tools()

        log.info("Groq | task=%s model=%s session=%s", task_type.value, model_name, session_id)

        async def _call() -> AgentResponse:
            effective_prompt = build_system_prompt(platform_system_note)
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": f"{session_id}:groq"}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from Groq")
                return AgentResponse(
                    content=content,
                    provider=self.provider,
                    model_name=model_name,
                    session_id=session_id,
                    task_type=task_type,
                    tokens_used=len(content) // 4,
                )

        return await self._retry(_call, session_id, max_retries)

    async def clear_memory(self, session_id: str) -> None:
        await self._clear_sqlite_memory(session_id, "groq")

    async def health_check(self) -> bool:
        """Ping Groq with a minimal request."""
        if not settings.groq_api_key:
            return False
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model="llama-3.1-8b-instant",
                groq_api_key=settings.groq_api_key,
                max_tokens=5,
            )
            result = await llm.ainvoke("Hi")
            return bool(result.content)
        except Exception as exc:
            log.warning("Groq health check failed: %s", exc)
            return False
