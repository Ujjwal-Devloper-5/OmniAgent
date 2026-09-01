"""
Anthropic Claude agent backend.
Excellent at creative writing, nuanced reasoning, and long documents.
System prompt pulled from shared base — no duplication.
"""

from __future__ import annotations

from core.agents.base import build_system_prompt, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)


class AnthropicAgent(BaseAgent):
    """Anthropic Claude agent (Sonnet for complex, Haiku for quick)."""

    provider = ModelProvider.ANTHROPIC

    def __init__(self) -> None:
        self._available = bool(settings.anthropic_api_key)

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
        needs_vision: bool = False,
        image_data: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> AgentResponse:
        if not settings.anthropic_api_key:
            return AgentResponse(
                content="Anthropic is not configured.",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        from langchain_anthropic import ChatAnthropic
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.prebuilt import create_react_agent
        from tools.registry import get_tools

        model_name = (
            settings.anthropic_model_fast
            if task_type == TaskType.QUICK
            else settings.anthropic_model
        )
        llm = ChatAnthropic(
            model=model_name,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.anthropic_max_tokens,
        )
        tools = get_tools()

        async def _call() -> AgentResponse:
            effective_prompt = await build_system_prompt(platform_system_note, session_id)
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": f"{session_id}:anthropic"}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from Anthropic")
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
        await self._clear_sqlite_memory(session_id, "anthropic")

    async def health_check(self) -> bool:
        if not settings.anthropic_api_key:
            return False
        try:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                model=settings.anthropic_model_fast,
                api_key=settings.anthropic_api_key,
                max_tokens=10,
            )
            result = await llm.ainvoke("Reply with OK")
            return bool(result.content)
        except Exception as exc:
            log.warning("Anthropic health check failed: %s", exc)
            return False
