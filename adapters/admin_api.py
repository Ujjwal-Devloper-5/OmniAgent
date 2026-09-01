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
            for q in list(_log_queues):
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(title="OmniAgent Admin API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.security import APIKeyHeader, APIKeyQuery

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
api_key_query = APIKeyQuery(name="token", auto_error=False)

async def verify_token(header_key: str = Depends(api_key_header), query_key: str = Depends(api_key_query)) -> str:
    if not settings.admin_api_secret:
        raise HTTPException(status_code=403, detail="Admin API disabled")
    
    token = None
    if header_key:
        token = header_key.replace("Bearer ", "")
    elif query_key:
        token = query_key
        
    if token != settings.admin_api_secret:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

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

@app.post("/api/models", dependencies=[Depends(verify_token)])
async def add_model(model_data: dict) -> dict:
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    data.setdefault("models", []).append(model_data)
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

@app.get("/api/config", dependencies=[Depends(verify_token)])
async def get_config() -> dict:
    # Return non-sensitive config
    safe_config = {}
    for k, v in settings.model_dump().items():
        if "token" not in k.lower() and "secret" not in k.lower() and "key" not in k.lower():
            safe_config[k] = v
    return safe_config

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
