"""
Ollama local model agent backend.
Runs entirely offline using local models like Llama 3, Mistral, Phi, etc.
Perfect as a final fallback when all cloud providers are down.
System prompt pulled from shared base — no duplication.
"""

from __future__ import annotations

from core.agents.base import SHARED_SYSTEM_PROMPT, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)


class OllamaAgent(BaseAgent):
    """Local Ollama agent — fully offline fallback."""

    provider = ModelProvider.OLLAMA
    _reachable: bool | None = None

    async def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                self._reachable = resp.status_code == 200
                return self._reachable
        except Exception:
            self._reachable = False
            return False

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 2,
        platform_system_note: str = "",
    ) -> AgentResponse:
        from langchain_ollama import ChatOllama
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.prebuilt import create_react_agent
        from tools.registry import get_tools

        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout,
        )
        tools = get_tools()

        async def _call() -> AgentResponse:
            effective_prompt = SHARED_SYSTEM_PROMPT + platform_system_note
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                )
                config = {"configurable": {"thread_id": f"{session_id}:ollama"}}
                inputs = {"messages": [("user", message)]}
                final_state = await agent.ainvoke(inputs, config=config)
                content = final_state["messages"][-1].content
                if not content or not content.strip():
                    raise ValueError("Empty response from Ollama")
                return AgentResponse(
                    content=content,
                    provider=self.provider,
                    model_name=settings.ollama_model,
                    session_id=session_id,
                    task_type=task_type,
                    tokens_used=len(content) // 4,
                )

        return await self._retry(_call, session_id, max_retries)

    async def clear_memory(self, session_id: str) -> None:
        await self._clear_sqlite_memory(session_id, "ollama")

    async def health_check(self) -> bool:
        return await self.is_available()
