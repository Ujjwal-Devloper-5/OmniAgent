"""
OmniAgent Admin API — FastAPI-powered management interface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from config import settings
from core.logger import get_logger
from core.model_registry import get_registry, _REGISTRY_PATH
from core.memory import get_memory
from core.user_settings import get_user_settings
from core.model_router import get_router

log = get_logger(__name__)

# ── Logging Setup ─────────────────────────────────────────────────────────────

_log_buffer: deque = deque(maxlen=500)
_log_queues: set[asyncio.Queue] = set()

class AdminLogHandler(logging.Handler):
    """Appends log lines to _log_buffer and broadcasts to SSE queues."""
    def __init__(self) -> None:
        super().__init__()
        # Format similar to console
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _log_buffer.append(msg)
            # Push to any active SSE listeners
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            for q in list(_log_queues):
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, msg)
                else:
                    q.put_nowait(msg)
        except Exception:
            pass  # Never crash the logging system

# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(title="OmniAgent Admin API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

from fastapi import Header
import secrets

async def verify_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Verify admin API token using constant-time comparison to prevent timing attacks."""
    if not settings.admin_api_secret:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled: ADMIN_API_SECRET is not configured"
        )
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Use: Authorization: Bearer <token>"
        )
    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token.encode(), settings.admin_api_secret.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}

@app.get("/api/status", dependencies=[Depends(verify_token)])
async def get_status() -> dict:
    router_health = await get_router().get_health_report_async()
    registry_summary = get_registry().get_registry_summary()
    return {
        "router_health": router_health,
        "registry_summary": registry_summary
    }

@app.get("/api/models", dependencies=[Depends(verify_token)])
async def list_models() -> dict:
    if not _REGISTRY_PATH.exists():
        return {"models": []}
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    return data

class ModelUpdate(BaseModel):
    intelligence: int
    speed: int
    tool_reliability: int
    vision: bool
    context_window: int
    tags: list[str]

from pydantic import BaseModel, Field as PydanticField

class ModelCreateRequest(BaseModel):
    id: str = PydanticField(..., min_length=1, max_length=200, description="Unique model identifier")
    provider: str = PydanticField(..., description="Provider name: gemini, openai, anthropic, groq, openrouter, ollama")
    capabilities: list[str] = PydanticField(default_factory=list)
    intelligence_score: float = PydanticField(default=5.0, ge=0.0, le=10.0)
    speed_score: float = PydanticField(default=5.0, ge=0.0, le=10.0)
    tool_reliability: float = PydanticField(default=5.0, ge=0.0, le=10.0)
    supports_vision: bool = False
    is_free_tier: bool = False
    notes: str = ""

@app.post("/api/models", dependencies=[Depends(verify_token)])
async def add_model(model_data: ModelCreateRequest) -> dict:
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    data.setdefault("models", []).append(model_data.model_dump())
    _REGISTRY_PATH.write_text(json.dumps(data, indent=2))
    
    # Reload
    health = await get_router().get_health_report_async()
    configured = {p for p, info in health.items() if info["configured"]}
    reg = get_registry()
    reg._models.clear()
    reg._load_registry()
    await reg.initialize(configured)
    return {"status": "added"}

@app.put("/api/models/{model_id}", dependencies=[Depends(verify_token)])
async def update_model(model_id: str, payload: ModelUpdate) -> dict:
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    found = False
    for m in data.get("models", []):
        if m["id"] == model_id:
            m.update(payload.model_dump())
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Model not found")
    _REGISTRY_PATH.write_text(json.dumps(data, indent=2))
    
    health = await get_router().get_health_report_async()
    configured = {p for p, info in health.items() if info["configured"]}
    reg = get_registry()
    reg._models.clear()
    reg._load_registry()
    await reg.initialize(configured)
    return {"status": "updated"}

@app.delete("/api/models/{model_id}", dependencies=[Depends(verify_token)])
async def delete_model(model_id: str) -> dict:
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    models = data.get("models", [])
    data["models"] = [m for m in models if m["id"] != model_id]
    _REGISTRY_PATH.write_text(json.dumps(data, indent=2))
    
    health = await get_router().get_health_report_async()
    configured = {p for p, info in health.items() if info["configured"]}
    reg = get_registry()
    reg._models.clear()
    reg._load_registry()
    await reg.initialize(configured)
    return {"status": "deleted"}

@app.get("/api/users", dependencies=[Depends(verify_token)])
async def list_users() -> list[dict]:
    return await get_user_settings().get_all()

@app.get("/api/users/{user_id}", dependencies=[Depends(verify_token)])
async def get_user(user_id: str) -> dict:
    return await get_user_settings().get(user_id)

class SystemPromptUpdate(BaseModel):
    system_prompt: str

@app.post("/api/users/{user_id}/system_prompt", dependencies=[Depends(verify_token)])
async def set_system_prompt(user_id: str, payload: SystemPromptUpdate) -> dict:
    await get_user_settings().upsert(user_id, system_prompt=payload.system_prompt)
    return {"status": "updated"}

@app.delete("/api/users/{user_id}/system_prompt", dependencies=[Depends(verify_token)])
async def delete_system_prompt(user_id: str) -> dict:
    await get_user_settings().upsert(user_id, system_prompt="")
    return {"status": "deleted"}

@app.get("/api/sessions", dependencies=[Depends(verify_token)])
async def list_sessions() -> list[dict]:
    return await get_memory().get_all_sessions()

@app.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_token)])
async def clear_session(session_id: str) -> dict:
    await get_memory().clear_session(session_id)
    return {"status": "cleared"}

@app.get("/api/logs", dependencies=[Depends(verify_token)])
async def get_logs() -> dict:
    # Return last 200
    lines = list(_log_buffer)[-200:]
    return {"logs": lines}

@app.get("/api/logs/stream", dependencies=[Depends(verify_token)])
async def stream_logs(request: Request):
    q = asyncio.Queue()
    _log_queues.add(q)
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                line = await q.get()
                yield {"data": line}
        finally:
            _log_queues.remove(q)
            
    return EventSourceResponse(event_generator())

@app.post("/api/reboot", dependencies=[Depends(verify_token)])
async def reboot_system() -> dict:
    log.warning("Admin requested system reboot. Exiting process...")
    # Return response before exiting
    asyncio.get_event_loop().call_later(1.0, lambda: sys.exit(0))
    return {"status": "rebooting"}

@app.get("/api/config")
async def get_config(_: None = Depends(verify_token)):
    """Return non-sensitive operational configuration settings."""
    # Explicit whitelist of settings safe to expose — NEVER expose secrets, keys, tokens, URLs with credentials
    SAFE_CONFIG_KEYS = {
        "bot_name", "bot_prefix", "log_level", "default_provider",
        "coding_provider", "creative_provider", "math_provider", "quick_provider",
        "fallback_order", "gemini_model", "gemini_model_pro", "gemini_model_flash",
        "gemini_temperature", "gemini_max_output_tokens",
        "openai_model", "openai_model_fast", "openai_temperature", "openai_max_tokens",
        "anthropic_model", "anthropic_model_fast", "anthropic_max_tokens",
        "ollama_base_url", "ollama_model", "ollama_timeout",
        "openrouter_model", "openrouter_temperature", "openrouter_max_tokens",
        "groq_temperature", "groq_max_tokens",
        "rate_limit_requests_per_minute", "rate_limit_tokens_per_day",
        "swarm_max_steps", "swarm_total_timeout_seconds", "swarm_agent_timeout_seconds",
        "swarm_max_dynamic_agents", "sandbox_cmd_timeout_seconds", "sandbox_ttl_seconds",
        "sandbox_memory_limit", "sandbox_cpu_quota", "sandbox_max_concurrent",
        "retention_reports_days", "retention_sandbox_volumes_days",
        "max_history_messages", "health_check_interval_seconds",
        "model_failure_threshold", "model_recovery_seconds",
        "log_max_bytes", "log_backup_count",
    }
    all_settings = settings.model_dump()
    return {k: v for k, v in all_settings.items() if k in SAFE_CONFIG_KEYS}

# Serve dashboard from / 
from fastapi.responses import HTMLResponse
@app.get("/")
async def serve_dashboard():
    dashboard_path = Path("dashboard/index.html")
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="Dashboard not found", status_code=404)

async def start_admin_api() -> None:
    """Start the FastAPI admin API on port 8080."""
    if not settings.admin_api_secret:
        log.warning("Admin API disabled — set ADMIN_API_SECRET to enable")
        return
        
    # Add handler
    root = logging.getLogger()
    has_admin = any(isinstance(h, AdminLogHandler) for h in root.handlers)
    if not has_admin:
        root.addHandler(AdminLogHandler())
        
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Admin API starting on http://0.0.0.0:8080")
    await server.serve()
