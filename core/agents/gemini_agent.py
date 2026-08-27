"""
Google Gemini Agent — Production Grade
═══════════════════════════════════════

Two distinct execution paths:

  PATH 1 — LangGraph ReAct (text tasks)
    Uses langchain-google-genai + LangGraph create_react_agent.
    Full tool-calling support (web_search, sandbox, calculator, etc.).
    Shared SQLite checkpointer for cross-model memory continuity.
    Model: gemini-2.5-flash (default) or gemini-2.5-pro for deep tasks.

  PATH 2 — Native Gemini SDK (vision/multimodal tasks)
    Uses google-genai library's native multimodal API.
    Accepts raw image bytes + MIME type — no URL scraping, real pixels.
    Conversations include image inline — Gemini sees the actual image.
    Falls back gracefully to text description if image download fails.

Key design decisions:
  - thread_id = session_id (shared with all providers for unified memory)
  - All imports are lazy (inside methods) to avoid circular import issues
  - health_check uses models.list — zero token cost
  - Vision path catches all exceptions and falls back cleanly to text
"""

from __future__ import annotations

import asyncio
import base64
from typing import Optional

from config import settings
from core.agents.base import (
    AgentResponse,
    BaseAgent,
    ModelProvider,
    TaskType,
    build_system_prompt,
)
from core.logger import get_logger

log = get_logger(__name__)

# Gemini MIME types we can handle natively
_SUPPORTED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/gif", "image/bmp", "image/tiff",
}


class GeminiAgent(BaseAgent):
    """
    Google Gemini agent.

    Supports text (with full tool-calling via LangGraph) and
    multimodal vision tasks (native Gemini SDK with real image bytes).
    """

    provider = ModelProvider.GEMINI

    def __init__(self) -> None:
        self._available = bool(settings.gemini_api_key)

    async def is_available(self) -> bool:
        return self._available

    def _pick_model(self, task_type: TaskType) -> str:
        """
        Select the Gemini model variant for this task.
        Pro for deep analysis/coding/math; Flash for everything else.
        Flash is also used for vision (it's natively multimodal).
        """
        heavy_tasks = {TaskType.ANALYSIS, TaskType.CODING, TaskType.MATH}
        if task_type in heavy_tasks:
            return settings.gemini_model_pro
        return settings.gemini_model_flash

    # ──────────────────────────────────────────────────────────────────────────
    # PATH 1 — LangGraph ReAct (text tasks with tool calling)
    # ──────────────────────────────────────────────────────────────────────────

    async def _call_with_tools(
        self,
        message: str,
        session_id: str,
        task_type: TaskType,
        platform_system_note: str,
        max_retries: int,
    ) -> AgentResponse:
        """Run the LangGraph ReAct agent with full tool support."""
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.prebuilt import create_react_agent
        from tools.registry import get_tools
        from core.context_manager import maybe_trim_context

        model_name = self._pick_model(task_type)
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=settings.gemini_temperature,
            google_api_key=settings.gemini_api_key,
            max_output_tokens=settings.gemini_max_output_tokens,
        )
        tools = get_tools()
        effective_prompt = build_system_prompt(platform_system_note)

        context_summary = await maybe_trim_context(session_id, settings.db_path)
        if context_summary:
            message = context_summary + "\n\n" + message

        async def _call() -> AgentResponse:
            async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
                agent = create_react_agent(
                    llm, tools,
                    checkpointer=checkpointer,
                    prompt=effective_prompt,
                )
                # UNIFIED thread_id — same as all other providers
                config = {"configurable": {"thread_id": session_id}}
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

        log.info("Gemini | task=%s model=%s session=%s (tools path)", task_type.value, model_name, session_id)
        return await self._retry(_call, session_id, max_retries)

    # ──────────────────────────────────────────────────────────────────────────
    # PATH 2 — Native Gemini SDK (vision/multimodal)
    # ──────────────────────────────────────────────────────────────────────────

    async def _call_vision(
        self,
        message: str,
        session_id: str,
        task_type: TaskType,
        platform_system_note: str,
        max_retries: int,
        image_data: Optional[bytes] = None,
        image_mime: str = "image/jpeg",
    ) -> AgentResponse:
        """
        Run multimodal Gemini using the native genai SDK.
        Passes real image bytes inline — Gemini actually sees the image.
        Falls back to text-only path if image data is unavailable.
        """
        if image_data is None:
            # No actual image bytes — treat as a text task
            log.info("Gemini vision | no image bytes available, falling back to text path")
            return await self._call_with_tools(
                message, session_id, task_type, platform_system_note, max_retries
            )

        model_name = settings.gemini_model_flash  # Flash = multimodal capable

        system_prompt = (
            "You are OmniAgent, a powerful multimodal AI assistant created by Ujjwal Kumar. "
            "Analyse images with extreme detail and accuracy. "
            "Be thorough, descriptive, and structured in your analysis. "
            "Format your response in markdown."
            + (f"\n\n{platform_system_note}" if platform_system_note else "")
        )

        async def _call() -> AgentResponse:
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt,
            )

            # Build the multimodal message parts
            image_part = {
                "inline_data": {
                    "mime_type": image_mime if image_mime in _SUPPORTED_IMAGE_MIMES else "image/jpeg",
                    "data": base64.b64encode(image_data).decode("utf-8"),
                }
            }
            content_parts = [image_part, message]

            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(content_parts),
            )

            content = response.text.strip() if response.text else ""
            if not content:
                raise ValueError("Empty vision response from Gemini")

            log.info(
                "Gemini vision | model=%s bytes=%d tokens≈%d",
                model_name, len(image_data), len(content) // 4,
            )
            return AgentResponse(
                content=content,
                provider=self.provider,
                model_name=model_name,
                session_id=session_id,
                task_type=task_type,
                tokens_used=len(content) // 4,
                has_media=True,
            )

        log.info(
            "Gemini | task=vision model=%s session=%s image_bytes=%d",
            model_name, session_id, len(image_data),
        )
        return await self._retry(_call, session_id, max_retries)

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    async def process_message(
        self,
        session_id: str,
        message: str,
        platform: str = "unknown",
        task_type: TaskType = TaskType.GENERAL,
        max_retries: int = 3,
        platform_system_note: str = "",
        needs_vision: bool = False,
        image_data: Optional[bytes] = None,
        image_mime: str = "image/jpeg",
    ) -> AgentResponse:
        if not settings.gemini_api_key:
            return AgentResponse(
                content="Gemini is not configured (no GEMINI_API_KEY).",
                provider=self.provider,
                model_name="none",
                session_id=session_id,
                error="No API key",
            )

        if needs_vision:
            return await self._call_vision(
                message=message,
                session_id=session_id,
                task_type=task_type,
                platform_system_note=platform_system_note,
                max_retries=max_retries,
                image_data=image_data,
                image_mime=image_mime,
            )

        return await self._call_with_tools(
            message=message,
            session_id=session_id,
            task_type=task_type,
            platform_system_note=platform_system_note,
            max_retries=max_retries,
        )

    async def clear_memory(self, session_id: str) -> None:
        # Use the shared unified thread_id (no suffix)
        import aiosqlite
        async with aiosqlite.connect(settings.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                try:
                    await conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = ?",
                        (session_id,),
                    )
                except Exception:
                    pass
            await conn.commit()
        log.info("Gemini memory cleared | session=%s", session_id)

    async def health_check(self) -> bool:
        """Check Gemini availability — uses models.list, zero token cost."""
        if not settings.gemini_api_key:
            return False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.gemini_api_key}",
                )
                return resp.status_code == 200
        except Exception as exc:
            log.warning("Gemini health check failed: %s", exc)
            return False
