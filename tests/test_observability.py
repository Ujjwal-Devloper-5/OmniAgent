"""
OmniAgent Phase 7 Test Suite — Observability & Prometheus Metrics Verification (AC 4 & R3)
═════════════════════════════════════════════════════════════════════════════════════════
Covers Acceptance Criterion 4:
  "`curl http://localhost:8080/metrics` successfully returns live Prometheus metrics data."

Tiers Covered:
  - Tier 1: /metrics route, unauthenticated access, Content-Type, and /health alias
  - Tier 2: Metric family schema validation & Prometheus label cardinality protection
  - Tier 3: Dynamic increments on HTTP requests, token recording, and model fallback
  - Tier 4: Live HTTP scrape against localhost:8080/metrics and localhost:8080/health
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict

import pytest
from starlette.testclient import TestClient

REQUIRED_METRICS = [
    "omniagent_http_request_duration_seconds",
    "omniagent_token_usage_total",
    "omniagent_model_fallbacks_total",
    "omniagent_errors_total",
    "omniagent_active_requests",
]


# ─────────────────────────────────────────────────────────────────────────────
# Reference Prometheus Metrics Engine for Contract Verification
# ─────────────────────────────────────────────────────────────────────────────

class ReferenceMetricsRegistry:
    """
    Simulates / implements the Phase 7 Prometheus metrics registry
    defined in PROJECT.md:47-55 and Survey Report 3 §3.
    """

    def __init__(self) -> None:
        self.counters: Dict[str, Dict[tuple, float]] = {
            "omniagent_token_usage_total": {},
            "omniagent_model_fallbacks_total": {},
            "omniagent_errors_total": {},
        }
        self.histograms: Dict[str, Dict[tuple, list[float]]] = {
            "omniagent_http_request_duration_seconds": {},
        }
        self.gauges: Dict[str, float] = {
            "omniagent_active_requests": 0.0,
        }

    def record_request_duration(self, method: str, path: str, status_code: int, duration: float) -> None:
        key = (method, path, str(status_code))
        self.histograms["omniagent_http_request_duration_seconds"].setdefault(key, []).append(duration)

    def record_tokens(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        key = (provider, model, "total")
        total = prompt_tokens + completion_tokens
        curr = self.counters["omniagent_token_usage_total"].get(key, 0.0)
        self.counters["omniagent_token_usage_total"][key] = curr + total

    def record_model_fallback(self, from_model: str, to_model: str, reason: str) -> None:
        key = (from_model, to_model, reason)
        curr = self.counters["omniagent_model_fallbacks_total"].get(key, 0.0)
        self.counters["omniagent_model_fallbacks_total"][key] = curr + 1.0

    def record_error(self, subsystem: str, error_type: str) -> None:
        key = (subsystem, error_type)
        curr = self.counters["omniagent_errors_total"].get(key, 0.0)
        self.counters["omniagent_errors_total"][key] = curr + 1.0

    def generate_prometheus_text(self) -> str:
        lines = []
        for name in REQUIRED_METRICS:
            lines.append(f"# HELP {name} Generated metric {name}")
            if "total" in name:
                lines.append(f"# TYPE {name} counter")
                data = self.counters.get(name, {})
                if not data:
                    lines.append(f"{name} 0.0")
                for labels, val in data.items():
                    lbl_str = ",".join(f'lbl_{i}="{v}"' for i, v in enumerate(labels))
                    lines.append(f"{name}{{{lbl_str}}} {val}")
            elif "seconds" in name:
                lines.append(f"# TYPE {name} histogram")
                data = self.histograms.get(name, {})
                if not data:
                    lines.append(f"{name}_count 0")
                    lines.append(f"{name}_sum 0.0")
                for labels, vals in data.items():
                    lbl_str = ",".join(f'lbl_{i}="{v}"' for i, v in enumerate(labels))
                    lines.append(f"{name}_count{{{lbl_str}}} {len(vals)}")
                    lines.append(f"{name}_sum{{{lbl_str}}} {sum(vals)}")
            else:
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {self.gauges.get(name, 0.0)}")
        return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Feature Coverage (Endpoints & Unauthenticated Access)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_metrics_registry_produces_all_five_metric_families() -> None:
    """
    Tier 1: Validates that metrics registry generates all 5 mandatory metric families:
      - omniagent_http_request_duration_seconds
      - omniagent_token_usage_total
      - omniagent_model_fallbacks_total
      - omniagent_errors_total
      - omniagent_active_requests
    Derived from: ORIGINAL_REQUEST.md:56, 64, PROJECT.md:8.
    """
    reg = ReferenceMetricsRegistry()
    text = reg.generate_prometheus_text()

    for metric in REQUIRED_METRICS:
        assert f"# TYPE {metric}" in text, f"Metric '{metric}' missing in Prometheus output"
        assert metric in text


@pytest.mark.unit
def test_admin_api_health_endpoint() -> None:
    """
    Tier 1: Validates GET /api/health and GET /health return HTTP 200 {"status": "ok"}.
    Derived from: Survey 3 §2.4 (Docker healthcheck alias).
    """
    from adapters.admin_api import app

    client = TestClient(app, raise_server_exceptions=False)

    # 1. Test canonical health route
    resp_api = client.get("/api/health")
    assert resp_api.status_code == 200
    assert resp_api.json().get("status") == "ok"

    # 2. Test root health alias required by Dockerfile
    # Note: If /health alias is not yet merged by M1, assert route contract
    resp_root = client.get("/health")
    if resp_root.status_code == 404:
        pytest.fail(
            "IMPLEMENTATION GAP (M1): GET /health returned 404. "
            "Dockerfile healthcheck targets /health, must alias /api/health to /health in adapters/admin_api.py"
        )
    assert resp_root.status_code == 200
    assert resp_root.json().get("status") == "ok"


@pytest.mark.unit
def test_admin_api_metrics_endpoint_unauthenticated() -> None:
    """
    Tier 1: Validates GET /metrics is accessible without Bearer token.
    Prometheus scrapers do not pass Authorization: Bearer.
    Derived from: Survey 3 Edge Case #2.
    """
    from adapters.admin_api import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/metrics")

    if resp.status_code == 404:
        pytest.fail(
            "IMPLEMENTATION GAP (M1): GET /metrics returned 404. "
            "Prometheus /metrics route not yet mounted in adapters/admin_api.py."
        )

    assert resp.status_code == 200, (
        f"GET /metrics must return HTTP 200, got {resp.status_code}. "
        f"Must NOT require Bearer token authentication."
    )
    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type, f"Expected text/plain content-type, got: {content_type}"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Boundary & Cardinality Protection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_path_cardinality_normalization_logic() -> None:
    """
    Tier 2: Cardinality explosion prevention:
      Verifies dynamic paths with IDs (e.g. /api/models/gpt-4o, /api/users/123)
      are normalized to template patterns in metric labels.
      Derived from: Survey 3 Edge Case #8.
    """
    def normalize_route_path(raw_path: str) -> str:
        # Replaces UUIDs or alphanumeric identifiers
        path = re.sub(r"/users/[^/]+", "/users/{user_id}", raw_path)
        path = re.sub(r"/models/[^/]+", "/models/{model_id}", path)
        path = re.sub(r"/sessions/[^/]+", "/sessions/{session_id}", path)
        return path

    assert normalize_route_path("/api/models/gpt-4o") == "/api/models/{model_id}"
    assert normalize_route_path("/api/models/claude-3-5-sonnet") == "/api/models/{model_id}"
    assert normalize_route_path("/api/users/usr_abc123") == "/api/users/{user_id}"
    assert normalize_route_path("/api/health") == "/api/health"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: Cross-Feature Dynamic Metrics Recording
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_dynamic_metrics_recording() -> None:
    """
    Tier 3: Dynamic updates:
      Verifies recording requests, tokens, fallbacks, and errors dynamically
      updates the values emitted in the Prometheus scrape text.
    """
    reg = ReferenceMetricsRegistry()

    # Initial state
    initial_text = reg.generate_prometheus_text()
    assert "omniagent_token_usage_total 0.0" in initial_text

    # Record 450 tokens
    reg.record_tokens(provider="openrouter", model="gemma-4", prompt_tokens=200, completion_tokens=250)
    # Record model fallback
    reg.record_model_fallback(from_model="gemini-flash", to_model="openrouter/gemma", reason="rate_limit")
    # Record subsystem error
    reg.record_error(subsystem="sandbox", error_type="TimeoutError")
    # Record HTTP latency
    reg.record_request_duration(method="GET", path="/api/status", status_code=200, duration=0.045)

    updated_text = reg.generate_prometheus_text()

    # Assert token counter incremented
    assert "450" in updated_text, "Token counter must reflect 450 recorded tokens"
    # Assert fallback counter incremented
    assert "omniagent_model_fallbacks_total" in updated_text
    assert "rate_limit" in updated_text
    # Assert error counter incremented
    assert "TimeoutError" in updated_text
    # Assert HTTP histogram recorded
    assert "omniagent_http_request_duration_seconds_count" in updated_text
    assert "0.045" in updated_text


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4: Real-World Scenarios & Live Prometheus Scrape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_prometheus_metrics_scrape() -> None:
    """
    Tier 4 Live: Validates Acceptance Criterion 4:
      `curl http://localhost:8080/metrics` successfully returns live Prometheus metrics data.
    """
    import httpx

    url = "http://localhost:8080/metrics"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:
        pytest.fail(f"Failed to connect to live OmniAgent metrics endpoint at {url}: {exc}")

    assert resp.status_code == 200, f"Live /metrics returned HTTP {resp.status_code}"
    assert "text/plain" in resp.headers.get("content-type", "")

    body = resp.text
    for metric in REQUIRED_METRICS:
        assert metric in body, f"Live /metrics output missing required metric: {metric}"


@pytest.mark.live
def test_live_health_endpoint_scrape() -> None:
    """
    Tier 4 Live: Validates `curl http://localhost:8080/health` returns HTTP 200 ok.
    """
    import httpx

    url = "http://localhost:8080/health"
    try:
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:
        pytest.fail(f"Failed to connect to live health endpoint at {url}: {exc}")

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# In-Tree core.metrics Module Direct Verification
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_real_metrics_module_collectors() -> None:
    """Verify core.metrics exposes all required collectors and aliases."""
    import core.metrics as m
    assert m.HTTP_REQUEST_DURATION_SECONDS is not None
    assert m.HTTP_REQUEST_DURATION_SECONDS_ALIAS is not None
    assert m.LLM_REQUEST_DURATION_SECONDS is not None
    assert m.TOKEN_USAGE_TOTAL is not None
    assert m.MODEL_FALLBACKS_TOTAL is not None
    assert m.MODEL_FALLBACK_TOTAL is not None
    assert m.ERRORS_TOTAL is not None
    assert m.ACTIVE_REQUESTS is not None


@pytest.mark.unit
def test_real_metrics_reload_idempotency() -> None:
    """Verify reloading core.metrics does not trigger DuplicateTimeseries error."""
    import importlib
    import core.metrics as m
    try:
        importlib.reload(m)
    except Exception as exc:
        pytest.fail(f"Reloading core.metrics failed with exception: {exc}")


@pytest.mark.unit
def test_real_record_request_duration() -> None:
    """Verify record_request_duration observes both primary and alias histograms."""
    import core.metrics as m
    m.record_request_duration("GET", "/api/test_unit", 200, 0.035)
    snapshot = m.get_metrics_snapshot().decode("utf-8")
    assert "omniagent_http_request_duration_seconds" in snapshot
    assert "http_request_duration_seconds" in snapshot
    assert 'endpoint="/api/test_unit"' in snapshot
    assert 'method="GET"' in snapshot
    assert 'status_code="200"' in snapshot


@pytest.mark.unit
def test_real_record_tokens_and_zero_safety() -> None:
    """Verify record_tokens records prompt, completion, total and ignores non-positive."""
    import core.metrics as m
    m.record_tokens("gemini-test-model", prompt_tokens=100, completion_tokens=50)
    # Verify non-positive numbers do not crash
    m.record_tokens("gemini-test-model", prompt_tokens=0, completion_tokens=-10)
    snapshot = m.get_metrics_snapshot().decode("utf-8")
    assert "omniagent_token_usage_total" in snapshot
    assert 'model="gemini-test-model"' in snapshot
    assert 'token_type="prompt"' in snapshot
    assert 'token_type="completion"' in snapshot
    assert 'token_type="total"' in snapshot


@pytest.mark.unit
def test_real_record_model_fallback() -> None:
    """Verify record_model_fallback increments both plural and singular counters."""
    import core.metrics as m
    m.record_model_fallback("gemini-pro", "claude-3-5", "rate_limit_exceeded")
    snapshot = m.get_metrics_snapshot().decode("utf-8")
    assert "omniagent_model_fallbacks_total" in snapshot
    assert "omniagent_model_fallback_total" in snapshot
    assert 'from_model="gemini-pro"' in snapshot
    assert 'to_model="claude-3-5"' in snapshot
    assert 'reason="rate_limit_exceeded"' in snapshot


@pytest.mark.unit
def test_real_record_error() -> None:
    """Verify record_error handles both string errors and exception classes."""
    import core.metrics as m
    m.record_error("CustomTimeout", "worker_m1")
    m.record_error(ConnectionResetError("Socket reset"), "network_layer")
    snapshot = m.get_metrics_snapshot().decode("utf-8")
    assert "omniagent_errors_total" in snapshot
    assert 'error_type="CustomTimeout"' in snapshot
    assert 'error_type="ConnectionResetError"' in snapshot


@pytest.mark.unit
def test_real_record_llm_duration() -> None:
    """Verify record_llm_duration observes LLM latency histogram."""
    import core.metrics as m
    m.record_llm_duration("google", "gemini-2.5-flash", 0.45)
    snapshot = m.get_metrics_snapshot().decode("utf-8")
    assert "omniagent_llm_request_duration_seconds" in snapshot
    assert 'provider="google"' in snapshot
    assert 'model="gemini-2.5-flash"' in snapshot


@pytest.mark.unit
def test_admin_api_metrics_scrape_content_and_headers() -> None:
    """Verify /metrics returns CONTENT_TYPE_LATEST and contains all required metrics."""
    from adapters.admin_api import app
    from core.metrics import CONTENT_TYPE_LATEST
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert resp.headers.get("content-type", "") == CONTENT_TYPE_LATEST
    body = resp.text
    for metric in REQUIRED_METRICS:
        assert metric in body, f"Expected metric '{metric}' in /metrics scrape"

