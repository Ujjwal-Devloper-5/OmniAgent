"""
OpenRouter agent backend.

OpenRouter provides a unified API for 200+ AI models, including many FREE ones.
It uses an OpenAI-compatible API so we use ChatOpenAI with a custom base URL.

Free models (no credits needed, as of 2025):
  - meta-llama/llama-3.1-8b-instruct:free
  - meta-llama/llama-3.2-3b-instruct:free
  - google/gemma-2-9b-it:free
  - google/gemma-3-12b-it:free
  - mistralai/mistral-7b-instruct:free
  - microsoft/phi-3-mini-128k-instruct:free
  - qwen/qwen-2-7b-instruct:free
  - deepseek/deepseek-r1:free
  - nousresearch/nous-capybara-7b:free

Get a free API key at: https://openrouter.ai/
"""

from __future__ import annotations

import asyncio

from core.agents.base import build_system_prompt, AgentResponse, BaseAgent, ModelProvider, TaskType
from core.logger import get_logger
from config import settings

log = get_logger(__name__)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAgent(BaseAgent):
    """
    OpenRouter agent — access to 200+ models including FREE ones.

    Uses the OpenAI-compatible API from openrouter.ai.
    Free models require no credits; premium models require a paid balance.
    """

    provider = ModelProvider.OPENROUTER

    def __init__(self) -> None:
        self._available = bool(settings.openrouter_api_key)

    async def is_available(self) -> bool:
        return self._available

    def _make_llm(self, model_name: str):
        """Create a ChatOpenAI instance pointed at OpenRouter."""
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=settings.openrouter_api_key,
            base_url=_OPENROUTER_BASE_URL,
            temperature=settings.openrouter_temperature,
            max_tokens=settings.openrouter_max_tokens,
            default_headers={
                "HTTP-Referer": "https://github.com/omniagent-homelab",
                "X-Title": "OmniAgent Homelab",
            },
        )

    def _pick_model(self, task_type: TaskType) -> str:
        """
        Select the best OpenRouter model for this task type.

        Priority rule: the FIRST model in openrouter_free_models_list is always
        the primary.  We only deviate for coding tasks where deepseek is better.
        We deliberately do NOT match 'nemotron' for anything — it ignores system
        prompts about tools and has hardcoded behaviour we can't override.
        """
        free_models = settings.openrouter_free_models_list
        primary = settings.openrouter_model  # configured primary model

        if not free_models:
            return primary

        # For coding: prefer deepseek/qwen if available, else primary
        if task_type == TaskType.CODING:
            coding_model = next(
                (m for m in free_models if any(x in m for x in ["deepseek", "qwen", "code"])),
                free_models[0],
            )
            return coding_model

        # For all other tasks: always use the primary/first model.
        # gemma-4-31b is first in our list — it's multimodal, supports tools,
        # and does NOT have hardcoded tool-list beliefs like nemotron.
        return free_models[0]


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
        if not settings.openrouter_api_key:
            return AgentResponse(
                content="OpenRouter is not configured.",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.prebuilt import create_react_agent
            from tools.registry import get_tools
        except ImportError as exc:
            raise RuntimeError(f"OpenRouter dependencies missing: {exc}")

        tools = get_tools()

        # Build the full candidate model list: primary + all fallbacks (deduplicated)
        primary = self._pick_model(task_type)
        free_list = settings.openrouter_free_models_list or []
        candidates: list[str] = []
        for m in [primary] + free_list:
            if m and m not in candidates:
                candidates.append(m)

        last_error: Exception | None = None

        from core.context_manager import maybe_trim_context
        context_summary = await maybe_trim_context(session_id, settings.db_path)
        if context_summary:
            message = context_summary + "\n\n" + message

        for model_name in candidates:
            llm = self._make_llm(model_name)
            log.info("OpenRouter | task=%s model=%s session=%s", task_type.value, model_name, session_id)

            async def _call(m=model_name, _llm=llm) -> AgentResponse:
                effective_prompt = build_system_prompt(platform_system_note)
                async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                    agent = create_react_agent(
                        _llm, tools, checkpointer=checkpointer, prompt=effective_prompt,
                    )
                    config = {"configurable": {"thread_id": session_id}}
                    inputs = {"messages": [("user", message)]}
                    final_state = await agent.ainvoke(inputs, config=config)
                    content = final_state["messages"][-1].content
                    if not content or not content.strip():
                        raise ValueError("Empty response from OpenRouter")
                    return AgentResponse(
                        content=content,
                        provider=self.provider,
                        model_name=m,
                        session_id=session_id,
                        task_type=task_type,
                        tokens_used=len(content) // 4,
                    )

            try:
                return await _call()
            except Exception as exc:
                err_str = str(exc)
                
                if self._is_corrupt_checkpoint_error(exc):
                    await self._heal_corrupt_checkpoint(session_id, settings.db_path)
                    try:
                        return await _call()
                    except Exception as heal_exc:
                        raise heal_exc
                        
                is_skip_error = any(x in err_str.lower() for x in [
                    "429", "rate", "rate-limited", "404", "unavailable", "not found"
                ])
                if is_skip_error:
                    log.warning("OpenRouter | model=%s failed/unavailable, trying next in list", model_name)
                    last_error = exc
                    continue  # ← try the next model
                # Unhandled error: raise immediately
                raise

        # All models exhausted
        raise RuntimeError(
            f"openrouter: all {len(candidates)} free models rate-limited. "
            f"Last error: {last_error}"
        )

    async def clear_memory(self, session_id: str) -> None:
        await self._clear_sqlite_memory(session_id, "openrouter")

    async def health_check(self) -> bool:
        """Ping OpenRouter with the fastest free model."""
        if not settings.openrouter_api_key:
            return False
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "HTTP-Referer": "https://github.com/omniagent-homelab",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{_OPENROUTER_BASE_URL}/models",
                    headers=headers,
                )
                return resp.status_code == 200
        except Exception as exc:
            log.warning("OpenRouter health check failed: %s", exc)
            return False

    async def list_free_models(self) -> list[dict]:
        """
        Fetch the current list of free models from OpenRouter.
        Returns list of model info dicts.
        """
        if not settings.openrouter_api_key:
            return []
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "HTTP-Referer": "https://github.com/omniagent-homelab",
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{_OPENROUTER_BASE_URL}/models",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            models = data.get("data", [])
            free = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "context_length": m.get("context_length", 0),
                }
                for m in models
                if m.get("pricing", {}).get("prompt") == "0"
                or ":free" in m.get("id", "")
            ]
            return sorted(free, key=lambda x: x["context_length"], reverse=True)
        except Exception as exc:
            log.warning("Failed to fetch OpenRouter free models: %s", exc)
            return []
