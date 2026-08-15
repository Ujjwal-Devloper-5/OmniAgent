"""
Google Gemini agent backend.
Uses Flash for quick tasks and Flash-default for everything else.
System prompt is pulled from the shared base to avoid duplication.
"""

from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent

from config import settings
from core.agents.base import SHARED_SYSTEM_PROMPT, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from tools.registry import get_tools

log = get_logger(__name__)


class GeminiAgent(BaseAgent):
    """Google Gemini agent (Flash for quick tasks, Flash as default)."""

    provider = ModelProvider.GEMINI

    def __init__(self) -> None:
        self._tools = get_tools()
        self._flash_llm: ChatGoogleGenerativeAI | None = None
        self._pro_llm: ChatGoogleGenerativeAI | None = None
        if settings.gemini_api_key:
            self._flash_llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model_flash,
                temperature=0.5,
                google_api_key=settings.gemini_api_key,
                max_output_tokens=settings.gemini_max_output_tokens,
            )
            self._pro_llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model_pro,
                temperature=settings.gemini_temperature,
                google_api_key=settings.gemini_api_key,
                max_output_tokens=settings.gemini_max_output_tokens,
            )

    async def is_available(self) -> bool:
        return bool(settings.gemini_api_key and self._flash_llm)

    def _pick_llm(self, task_type: TaskType) -> ChatGoogleGenerativeAI:
        """Use Pro for complex tasks if configured, Flash for everything else."""
        if task_type in (TaskType.ANALYSIS, TaskType.CODING, TaskType.MATH) and self._pro_llm:
            return self._pro_llm
        return self._flash_llm  # type: ignore[return-value]

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 3,
        platform_system_note: str = "",
    ) -> AgentResponse:
        if not self._flash_llm:
            return AgentResponse(
                content="Gemini is not configured.",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        llm = self._pick_llm(task_type)
        model_name = llm.model

        async def _call() -> AgentResponse:
            # Build effective system prompt: shared base + transient platform note
            effective_prompt = SHARED_SYSTEM_PROMPT
            if platform_system_note:
                effective_prompt = SHARED_SYSTEM_PROMPT + platform_system_note
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm,
                    self._tools,
                    checkpointer=checkpointer,
                    prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": f"{session_id}:gemini"}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from Gemini")
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
        await self._clear_sqlite_memory(session_id, "gemini")

    async def health_check(self) -> bool:
        if not self._flash_llm:
            return False
        try:
            result = await self._flash_llm.ainvoke("Reply with OK")
            return bool(result.content)
        except Exception as exc:
            log.warning("Gemini health check failed: %s", exc)
            return False
