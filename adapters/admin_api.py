"""
OmniAgent Admin API — FastAPI-powered management interface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

_AUTH_FAILURES: dict[str, list[float]] = defaultdict(list)
_AUTH_MAX_ATTEMPTS = 5
_AUTH_WINDOW_SECONDS = 900  # 15 minutes

def _is_auth_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    window_start = now - _AUTH_WINDOW_SECONDS
    _AUTH_FAILURES[client_ip] = [t for t in _AUTH_FAILURES[client_ip] if t > window_start]
    return len(_AUTH_FAILURES[client_ip]) >= _AUTH_MAX_ATTEMPTS

def _record_auth_failure(client_ip: str) -> None:
    _AUTH_FAILURES[client_ip].append(time.monotonic())

import uvicorn
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.routing import Match
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from config import settings
from core.logger import (
    get_logger,
    get_correlation_id,
    set_correlation_id,
    reset_correlation_id,
    correlation_id_var,
    JSONFormatter,
)
from core.metrics import (
    CONTENT_TYPE_LATEST,
    generate_latest,
    record_request_duration,
    record_error,
    inc_active_requests,
    dec_active_requests,
)
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
        self.setFormatter(JSONFormatter())

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

# ── Middlewares ───────────────────────────────────────────────────────────────


_CORRELATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_PARAM_DELIMITER_REGEX = re.compile(r"[^a-zA-Z0-9]+")

_SAFE_PARAM_NAMES = frozenset({
    # Common operational & pagination
    "page",
    "limit",
    "offset",
    "count",
    "cursor",
    # Safe keys & indexes
    "sort_key",
    "sort-key",
    "sortkey",
    "primary_key",
    "primary-key",
    "primarykey",
    "foreign_key",
    "foreign-key",
    "foreignkey",
    "cache_key",
    "cache-key",
    "cachekey",
    "partition_key",
    "partition-key",
    "partitionkey",
    "routing_key",
    "routing-key",
    "routingkey",
    "id_key",
    "id-key",
    "idkey",
    "search_key",
    "search-key",
    "searchkey",
    "group_key",
    "group-key",
    "groupkey",
    "sharding_key",
    "sharding-key",
    "shardingkey",
    "query_key",
    "query-key",
    "querykey",
    "filter_key",
    "filter-key",
    "filterkey",
    "item_key",
    "item-key",
    "itemkey",
    "translation_key",
    "translation-key",
    "public_key",
    "public-key",
    "publickey",
    # Token metrics & limits
    "max_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "token_count",
    # Lexical non-sensitive words
    "author",
    "author_id",
    "monkey",
    "turkey",
    "keyboard",
    "secretary",
})

_SAFE_KEY_PREFIXES = frozenset({
    "sort",
    "primary",
    "foreign",
    "cache",
    "partition",
    "routing",
    "id",
    "search",
    "group",
    "sharding",
    "query",
    "filter",
    "item",
    "translation",
    "public",
    "max",
})

_SAFE_PREFIXES = ("sort_", "primary_", "cache_", "max_")

_SENSITIVE_PARAM_NAMES = frozenset({
    # Core secret keywords
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "signature",
    "sig",
    "passwd",
    "pwd",
    "passcode",
    "passphrase",
    "credential",
    "credentials",
    "bearer",
    "key",
    # Specific compound credentials
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "private_key",
    "secret_key",
    "access_key",
    "auth_key",
    "cert",
    "certificate",
    "privatekey",
    "secretkey",
    "accesskey",
    "authkey",
})

_SUBSTRING_SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "passcode",
    "secret",
    "token",
    "apikey",
    "bearer",
)

_SAFE_SUBSTRINGS_FOR_MASKING = (
    "max_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "token_count",
    "secretary",
    "sort_key",
    "sort-key",
    "sortkey",
    "primary_key",
    "cache_key",
    "foreign_key",
    "routing_key",
    "partition_key",
    "public_key",
    "keyboard",
    "monkey",
    "turkey",
    "author_id",
    "author",
)


def _is_sensitive_param_name(key: str) -> bool:
    """Check if query parameter key indicates sensitive credential data."""
    if not key:
        return False
    k = key.lower().strip()

    # 1. Fast-path: check explicit safe operational whitelist
    if k in _SAFE_PARAM_NAMES:
        return False

    # 2. Safe prefix check (sort_, primary_, cache_, max_)
    if k.startswith(_SAFE_PREFIXES):
        return False

    # 3. Exact match against sensitive keywords
    if k in _SENSITIVE_PARAM_NAMES:
        return True

    # 4. Pre-split camelCase / PascalCase transitions
    # e.g. authKey -> auth Key, sortKey -> sort Key, getHTTPResponse -> get HTTP Response
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)

    # 5. Universal non-alphanumeric delimiter splitting
    tokens = [p for p in _PARAM_DELIMITER_REGEX.split(s.lower()) if p]

    # 6. Check token set against _SENSITIVE_PARAM_NAMES with safe prefix context for "key"
    for i, t in enumerate(tokens):
        if t in _SENSITIVE_PARAM_NAMES:
            # Special protection for "key": ignore if preceded by safe operational prefix (e.g. sort_key, primary_key)
            if t == "key" and i > 0 and tokens[i - 1] in _SAFE_KEY_PREFIXES:
                continue
            return True

    # 7. Substring fallback for compound words without delimiter (e.g. secret, password, token, apikey, bearer)
    # Mask known safe operational substrings first to avoid false positives (e.g. max_tokens containing "token")
    k_sub = k
    for safe in _SAFE_SUBSTRINGS_FOR_MASKING:
        k_sub = k_sub.replace(safe, "")

    for strong in _SUBSTRING_SENSITIVE_KEYWORDS:
        if strong in k_sub:
            return True

    return False


def _sanitize_query_params(query_params: Any) -> str:
    """Sanitize query parameters by redacting sensitive values."""
    if not query_params:
        return ""
    try:
        items = query_params.multi_items() if hasattr(query_params, "multi_items") else query_params.items()
        sanitized = [
            (k, "[REDACTED]" if _is_sensitive_param_name(k) else v)
            for k, v in items
        ]
        return urllib.parse.urlencode(sanitized, safe="[]:/")
    except Exception:
        return "[UNPARSEABLE_QUERY_PARAMS]"


def _validate_or_generate_correlation_id(raw_cid: str | None) -> str:
    """
    Validate incoming correlation ID against safe regex ^[a-zA-Z0-9_-]{1,64}$.
    If missing, empty, or failing regex validation, generates a new UUID4 string.
    """
    if raw_cid:
        stripped = raw_cid.strip()
        if _CORRELATION_ID_REGEX.match(stripped):
            return stripped
    return str(uuid.uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware for correlation ID extraction, generation, and propagation.
    - Extracts 'X-Correlation-ID' (or fallback 'X-Request-ID') from request headers.
    - If missing or empty, generates a new UUID4 string.
    - Binds ID to contextvars (correlation_id_var) and request.state.correlation_id.
    - Sets 'X-Correlation-ID' header on outgoing response.
    - Logs start, completion, or error with execution timing.
    - Restores contextvar in finally block.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        raw_cid = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        correlation_id = _validate_or_generate_correlation_id(raw_cid)

        token = set_correlation_id(correlation_id)
        try:
            request.state.correlation_id = correlation_id
            client_ip = request.client.host if request.client else "unknown"
            start_time = time.perf_counter()

            log.info(
                "HTTP request started: %s %s",
                request.method,
                request.url.path,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "client_ip": client_ip,
                    "query_params": _sanitize_query_params(request.query_params),
                    "correlation_id": correlation_id,
                },
            )

            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            response.headers["X-Correlation-ID"] = correlation_id

            log.info(
                "HTTP request finished: %s %s status=%d duration=%.2fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "correlation_id": correlation_id,
                },
            )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2) if "start_time" in locals() else 0.0
            log.error(
                "HTTP request error: %s %s error=%s duration=%.2fms",
                request.method,
                request.url.path,
                str(exc),
                duration_ms,
                exc_info=True,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "duration_ms": duration_ms,
                    "correlation_id": correlation_id,
                },
            )
            raise
        finally:
            reset_correlation_id(token)


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """ASGI middleware measuring request duration, active requests, and HTTP errors."""

    async def dispatch(self, request: Request, call_next) -> Response:
        inc_active_requests()
        start_time = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            record_error(error_type=exc.__class__.__name__, component="http_server")
            raise
        finally:
            duration = time.perf_counter() - start_time
            endpoint = self._resolve_endpoint_path(request)
            record_request_duration(
                method=request.method,
                endpoint=endpoint,
                status_code=status_code,
                duration_seconds=duration,
            )
            dec_active_requests()

    def _resolve_endpoint_path(self, request: Request) -> str:
        """Resolve route template to prevent high cardinality label explosion."""
        route = request.scope.get("route")
        if route and hasattr(route, "path"):
            return route.path

        app = request.app
        for r in getattr(app, "routes", []):
            match, _ = r.matches(request.scope)
            if match == Match.FULL:
                return getattr(r, "path", request.url.path)

        path = request.url.path
        if path in {"/metrics", "/health", "/api/health", "/"}:
            return path
        return "/unmatched"


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
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Correlation-ID",
        "X-Request-ID",
    ],
    expose_headers=["X-Correlation-ID", "X-Request-ID"],
)

app.add_middleware(PrometheusMetricsMiddleware)
app.add_middleware(CorrelationIdMiddleware)

from fastapi import Header
import secrets

async def verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Verify admin API token using constant-time comparison to prevent timing attacks."""
    client_ip = request.client.host if request.client else "0.0.0.0"
    if _is_auth_rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed auth attempts. Locked out for {_AUTH_WINDOW_SECONDS // 60} minutes."
        )

    if not settings.admin_api_secret:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled: ADMIN_API_SECRET is not configured"
        )
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token:
        _record_auth_failure(client_ip)
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Use: Authorization: Bearer <token>"
        )
    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token.encode(), settings.admin_api_secret.encode()):
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid token")

# ── Observability & Health Endpoints ──────────────────────────────────────────

@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Expose Prometheus plain-text metrics (unauthenticated for scraper)."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )

@app.get("/health", tags=["Observability"])
async def root_health_check() -> dict:
    """Root health check alias for Docker HEALTHCHECK and load balancers."""
    return {"status": "ok", "version": "2.0.0"}

@app.get("/api/health")
async def health_check() -> dict:
    """API health check."""
    return {"status": "ok", "version": "2.0.0"}

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

class ConfigUpdateRequest(BaseModel):
    """Schema for hot-reload config updates (non-sensitive fields only)."""
    routing_policy: str | None = None
    sandbox_ttl_seconds: int | None = None
    sandbox_max_concurrent: int | None = None
    rate_limit_rpm: int | None = None
    rate_limit_tpd: int | None = None


@app.post("/api/config/update")
async def update_config(
    req: ConfigUpdateRequest,
    _: str = Depends(verify_token),
) -> dict:
    """Hot-reload config — update non-sensitive settings without restart."""
    from config import settings
    updated: dict = {}
    errors: dict = {}

    if req.routing_policy is not None:
        valid = {"AUTO", "ECO", "SPEED", "QUALITY", "OFFLINE"}
        if req.routing_policy.upper() not in valid:
            errors["routing_policy"] = f"Must be one of: {sorted(valid)}"
        else:
            settings.routing_policy = req.routing_policy.upper()
            try:
                from core.model_router import get_router
                get_router()._settings.routing_policy = settings.routing_policy
            except Exception:
                pass
            updated["routing_policy"] = settings.routing_policy

    if req.sandbox_ttl_seconds is not None:
        if 60 <= req.sandbox_ttl_seconds <= 3600:
            settings.sandbox_ttl_seconds = req.sandbox_ttl_seconds
            updated["sandbox_ttl_seconds"] = req.sandbox_ttl_seconds
        else:
            errors["sandbox_ttl_seconds"] = "Must be 60–3600"

    if req.sandbox_max_concurrent is not None:
        if 1 <= req.sandbox_max_concurrent <= 50:
            settings.sandbox_max_concurrent = req.sandbox_max_concurrent
            updated["sandbox_max_concurrent"] = req.sandbox_max_concurrent
        else:
            errors["sandbox_max_concurrent"] = "Must be 1–50"

    if req.rate_limit_rpm is not None:
        if 1 <= req.rate_limit_rpm <= 1000:
            settings.rate_limit_rpm = req.rate_limit_rpm
            updated["rate_limit_rpm"] = req.rate_limit_rpm
        else:
            errors["rate_limit_rpm"] = "Must be 1–1000"

    if req.rate_limit_tpd is not None:
        if 1000 <= req.rate_limit_tpd <= 10_000_000:
            settings.rate_limit_tpd = req.rate_limit_tpd
            updated["rate_limit_tpd"] = req.rate_limit_tpd
        else:
            errors["rate_limit_tpd"] = "Must be 1000–10,000,000"

    status = "partial" if (updated and errors) else ("ok" if updated else "no_change")
    return {"status": status, "updated": updated, "errors": errors}

@app.get("/api/mcp/status")
async def get_mcp_status(_: str = Depends(verify_token)) -> dict:
    """Per-server MCP health with circuit breaker state."""
    try:
        from tools.mcp_manager import get_mcp_manager
        mgr = get_mcp_manager()
        return {
            "available": mgr.is_available(),
            "servers": mgr.get_server_status(),
            "total_tools": len(mgr.get_tools()),
        }
    except Exception as exc:
        return {"available": False, "servers": {}, "error": str(exc)}

@app.get("/api/models/status")
async def get_model_status(_: str = Depends(verify_token)) -> dict:
    """Live Pareto scores for all registry models."""
    try:
        from core.model_registry import get_registry
        registry = get_registry()
        ranked = registry.get_ranked_list()
        return {
            "total": len(ranked),
            "models": [
                {
                    "id": m.id,
                    "provider": m.provider.value if hasattr(m.provider, "value") else str(m.provider),
                    "score": round(registry.compute_score(m), 3),
                    "available": m.available,
                    "failures": m.consecutive_failures,
                    "intelligence": m.intelligence,
                    "is_free": getattr(m, "is_free_tier", False),
                    "tags": list(getattr(m, "tags", [])),
                }
                for m in ranked
            ],
        }
    except Exception as exc:
        return {"error": str(exc), "models": []}

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
