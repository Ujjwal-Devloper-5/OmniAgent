"""
OmniAgent Prometheus Metrics Module.
Centralized metrics registry, collectors, and helper instrumentation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    CONTENT_TYPE_LATEST,
    generate_latest,
)

log = logging.getLogger(__name__)

# ── Safe Registration Helpers (Reload & Test Safe) ───────────────────────────

def _safe_register_counter(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Counter:
    """Register a Counter or return the existing collector if already registered."""
    if registry and hasattr(registry, "_names_to_collectors"):
        existing = registry._names_to_collectors.get(name) or registry._names_to_collectors.get(f"{name}_total")
        if existing is not None and isinstance(existing, Counter):
            return existing
    try:
        return Counter(name, documentation, labelnames=labelnames, registry=registry)
    except ValueError:
        if registry and hasattr(registry, "_names_to_collectors"):
            existing = registry._names_to_collectors.get(name) or registry._names_to_collectors.get(f"{name}_total")
            if existing is not None:
                return existing
        raise


def _safe_register_histogram(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
    buckets: tuple[float, ...] = Histogram.DEFAULT_BUCKETS,
    registry: CollectorRegistry = REGISTRY,
) -> Histogram:
    """Register a Histogram or return the existing collector if already registered."""
    if registry and hasattr(registry, "_names_to_collectors"):
        existing = registry._names_to_collectors.get(name) or registry._names_to_collectors.get(f"{name}_bucket")
        if existing is not None and isinstance(existing, Histogram):
            return existing
    try:
        return Histogram(name, documentation, labelnames=labelnames, buckets=buckets, registry=registry)
    except ValueError:
        if registry and hasattr(registry, "_names_to_collectors"):
            existing = registry._names_to_collectors.get(name) or registry._names_to_collectors.get(f"{name}_bucket")
            if existing is not None:
                return existing
        raise


def _safe_register_gauge(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
    registry: CollectorRegistry = REGISTRY,
) -> Gauge:
    """Register a Gauge or return the existing collector if already registered."""
    if registry and hasattr(registry, "_names_to_collectors"):
        existing = registry._names_to_collectors.get(name)
        if existing is not None and isinstance(existing, Gauge):
            return existing
    try:
        return Gauge(name, documentation, labelnames=labelnames, registry=registry)
    except ValueError:
        if registry and hasattr(registry, "_names_to_collectors"):
            existing = registry._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise


# ── Metric Definitions ────────────────────────────────────────────────────────

# 1. HTTP Request Latency
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HTTP_REQUEST_DURATION_SECONDS = _safe_register_histogram(
    name="omniagent_http_request_duration_seconds",
    documentation="HTTP request latency in seconds",
    labelnames=("method", "endpoint", "status_code"),
    buckets=HTTP_BUCKETS,
)

# Alias for PROJECT.md contract compatibility
HTTP_REQUEST_DURATION_SECONDS_ALIAS = _safe_register_histogram(
    name="http_request_duration_seconds",
    documentation="HTTP request latency in seconds (contract alias)",
    labelnames=("method", "endpoint", "status_code"),
    buckets=HTTP_BUCKETS,
)

# 2. LLM Call Latency
LLM_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)

LLM_REQUEST_DURATION_SECONDS = _safe_register_histogram(
    name="omniagent_llm_request_duration_seconds",
    documentation="LLM generation latency in seconds",
    labelnames=("provider", "model"),
    buckets=LLM_BUCKETS,
)

# 3. Token Consumption Counter
TOKEN_USAGE_TOTAL = _safe_register_counter(
    name="omniagent_token_usage_total",
    documentation="Total AI tokens consumed",
    labelnames=("model", "token_type"),
)

# 4. Model & Provider Fallbacks Counter (both plural and singular for compatibility)
MODEL_FALLBACKS_TOTAL = _safe_register_counter(
    name="omniagent_model_fallbacks_total",
    documentation="Total model and provider fallbacks triggered",
    labelnames=("from_model", "to_model", "reason"),
)

MODEL_FALLBACK_TOTAL = _safe_register_counter(
    name="omniagent_model_fallback_total",
    documentation="Total model and provider fallbacks triggered (contract alias)",
    labelnames=("from_model", "to_model", "reason"),
)

# 5. Subsystem Errors Counter
ERRORS_TOTAL = _safe_register_counter(
    name="omniagent_errors_total",
    documentation="Total subsystem errors recorded",
    labelnames=("error_type", "component"),
)

# 6. Active In-Flight Requests Gauge
ACTIVE_REQUESTS = _safe_register_gauge(
    name="omniagent_active_requests",
    documentation="Number of active in-flight requests",
)


# ── High-Level Recording Functions ───────────────────────────────────────────

def record_request_duration(
    method: str,
    endpoint: str,
    status_code: int | str,
    duration_seconds: float,
) -> None:
    """Record HTTP request latency and status."""
    try:
        norm_method = str(method).upper()
        norm_endpoint = str(endpoint)
        norm_status = str(status_code)
        duration = max(0.0, float(duration_seconds))
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=norm_method,
            endpoint=norm_endpoint,
            status_code=norm_status,
        ).observe(duration)
        HTTP_REQUEST_DURATION_SECONDS_ALIAS.labels(
            method=norm_method,
            endpoint=norm_endpoint,
            status_code=norm_status,
        ).observe(duration)
    except Exception as exc:
        log.debug("Failed to record request duration metric: %s", exc)


def record_tokens(
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    *args: Any,
    provider: str = "unknown",
    total_tokens: Optional[int] = None,
    **kwargs: Any,
) -> None:
    """
    Record token consumption across prompt, completion, and total.
    Supports signatures:
      - record_tokens(model, prompt_tokens, completion_tokens)
      - record_tokens(provider, model, prompt_tokens, completion_tokens)
      - record_tokens(model=..., prompt_tokens=..., completion_tokens=...)
    """
    try:
        # Handle 4-arg signature: record_tokens(provider, model, prompt_tokens, completion_tokens)
        if args and len(args) == 2 and isinstance(model, str) and isinstance(prompt_tokens, str):
            # model was actually provider, prompt_tokens was model, args[0] was prompt, args[1] was completion
            m = str(prompt_tokens)
            p_tok = int(args[0])
            c_tok = int(args[1])
        elif kwargs.get("model"):
            m = str(kwargs["model"])
            p_tok = int(kwargs.get("prompt_tokens", prompt_tokens))
            c_tok = int(kwargs.get("completion_tokens", completion_tokens))
        else:
            m = str(model)
            p_tok = int(prompt_tokens)
            c_tok = int(completion_tokens)

        if p_tok > 0:
            TOKEN_USAGE_TOTAL.labels(model=m, token_type="prompt").inc(p_tok)
        if c_tok > 0:
            TOKEN_USAGE_TOTAL.labels(model=m, token_type="completion").inc(c_tok)

        calc_total = total_tokens if total_tokens is not None else (max(0, p_tok) + max(0, c_tok))
        if calc_total > 0:
            TOKEN_USAGE_TOTAL.labels(model=m, token_type="total").inc(calc_total)
    except Exception as exc:
        log.debug("Failed to record token usage metric: %s", exc)


def record_model_fallback(
    from_model: str,
    to_model: str,
    reason: str = "unknown",
) -> None:
    """Record a model or provider fallback event."""
    try:
        f = str(from_model)
        t = str(to_model)
        r = str(reason) if reason else "unknown"
        MODEL_FALLBACKS_TOTAL.labels(from_model=f, to_model=t, reason=r).inc()
        MODEL_FALLBACK_TOTAL.labels(from_model=f, to_model=t, reason=r).inc()
    except Exception as exc:
        log.debug("Failed to record model fallback metric: %s", exc)


def record_error(
    error_type: Any = "UnknownError",
    component: str = "unknown",
    *,
    subsystem: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """
    Record a subsystem error event.
    Accepts error_type as string or Exception, and component or subsystem.
    """
    try:
        err = error_type or kwargs.get("error_type") or "UnknownError"
        err_str = err.__class__.__name__ if isinstance(err, Exception) else str(err)
        comp = component if component != "unknown" else (subsystem or kwargs.get("component") or "unknown")
        ERRORS_TOTAL.labels(error_type=err_str, component=str(comp)).inc()
    except Exception as exc:
        log.debug("Failed to record error metric: %s", exc)


def inc_active_requests(endpoint: str = "all") -> None:
    """Increment gauge of active in-flight requests."""
    try:
        ACTIVE_REQUESTS.inc()
    except Exception as exc:
        log.debug("Failed to increment active requests metric: %s", exc)


def dec_active_requests(endpoint: str = "all") -> None:
    """Decrement gauge of active in-flight requests."""
    try:
        ACTIVE_REQUESTS.dec()
    except Exception as exc:
        log.debug("Failed to decrement active requests metric: %s", exc)


def record_llm_duration(
    provider: str,
    model: str,
    duration_seconds: float,
) -> None:
    """Record LLM generation latency."""
    try:
        LLM_REQUEST_DURATION_SECONDS.labels(
            provider=str(provider),
            model=str(model),
        ).observe(max(0.0, float(duration_seconds)))
    except Exception as exc:
        log.debug("Failed to record LLM latency metric: %s", exc)


def get_metrics_snapshot() -> bytes:
    """Generate Prometheus exposition text format bytes."""
    return generate_latest()


__all__ = [
    "REGISTRY",
    "CONTENT_TYPE_LATEST",
    "generate_latest",
    "HTTP_REQUEST_DURATION_SECONDS",
    "HTTP_REQUEST_DURATION_SECONDS_ALIAS",
    "LLM_REQUEST_DURATION_SECONDS",
    "TOKEN_USAGE_TOTAL",
    "MODEL_FALLBACKS_TOTAL",
    "MODEL_FALLBACK_TOTAL",
    "ERRORS_TOTAL",
    "ACTIVE_REQUESTS",
    "record_request_duration",
    "record_tokens",
    "record_model_fallback",
    "record_error",
    "inc_active_requests",
    "dec_active_requests",
    "record_llm_duration",
    "get_metrics_snapshot",
]
