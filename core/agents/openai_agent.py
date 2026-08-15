"""
OpenAI GPT agent backend.
Uses GPT-4o for complex tasks and GPT-4o-mini for quick ones.
System prompt pulled from shared base — no duplication.
"""

from __future__ import annotations

from core.agents.base import SHARED_SYSTEM_PROMPT, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)


class OpenAIAgent(BaseAgent):
    """OpenAI GPT agent (GPT-4o for complex, GPT-4o-mini for quick)."""

    provider = ModelProvider.OPENAI

    def __init__(self) -> None:
        self._available = bool(settings.openai_api_key)

    async def is_available(self) -> bool:
        return self._available

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 3,
        platform_system_note: str = "",
    ) -> AgentResponse:
        if not settings.openai_api_key:
            return AgentResponse(
                content="OpenAI is not configured.",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        from langchain_openai import ChatOpenAI
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.prebuilt import create_react_agent
        from tools.registry import get_tools

        model_name = (
            settings.openai_model_fast
            if task_type == TaskType.QUICK
            else settings.openai_model
        )
        llm = ChatOpenAI(
            model=model_name,
            temperature=settings.openai_temperature,
            api_key=settings.openai_api_key,
            max_tokens=settings.openai_max_tokens,
        )
        tools = get_tools()

        async def _call() -> AgentResponse:
            effective_prompt = SHARED_SYSTEM_PROMPT + platform_system_note
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": f"{session_id}:openai"}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from OpenAI")
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
        await self._clear_sqlite_memory(session_id, "openai")

    async def health_check(self) -> bool:
        if not settings.openai_api_key:
            return False
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=settings.openai_model_fast,
                api_key=settings.openai_api_key,
                max_tokens=10,
            )
            result = await llm.ainvoke("Reply with OK")
            return bool(result.content)
        except Exception as exc:
            log.warning("OpenAI health check failed: %s", exc)
            return False
