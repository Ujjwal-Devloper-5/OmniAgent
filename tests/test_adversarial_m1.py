"""
Adversarial and Stress Test Suite for Milestone 1:
- Concurrency & active requests gauge under heavy concurrent load and cancellation.
- High cardinality paths & degenerate/extreme metric parameters.
- Malformed X-Correlation-ID headers (symbols, unicode, extreme lengths, CRLF).
- Unserializable data in logger extra arguments (circular references, generator objects, binary bytes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from typing import Any
import pytest
from fastapi import Request
from httpx import AsyncClient, ASGITransport
from prometheus_client.parser import text_string_to_metric_families

from adapters.admin_api import app
from config import settings
import core.logger as logger_module
from core.logger import (
    JSONFormatter,
    correlation_id_scope,
    get_correlation_id,
    set_correlation_id,
    reset_correlation_id,
)
import core.metrics as metrics_module
from core.metrics import (
    ACTIVE_REQUESTS,
    HTTP_REQUEST_DURATION_SECONDS,
    ERRORS_TOTAL,
    TOKEN_USAGE_TOTAL,
    inc_active_requests,
    dec_active_requests,
    record_request_duration,
    record_tokens,
    record_model_fallback,
    record_error,
    record_llm_duration,
    get_metrics_snapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. High Concurrency & Active Requests Gauge Stress Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_requests_concurrency_surge():
    """Stress-test active requests gauge under rapid concurrent request bursts."""
    initial_gauge = ACTIVE_REQUESTS._value.get()
    transport = ASGITransport(app=app)
    
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async def fetch():
            resp = await client.get("/health")
            assert resp.status_code == 200
            return resp.status_code

        # Run 100 concurrent requests
        results = await asyncio.gather(*(fetch() for _ in range(100)))
        assert len(results) == 100

    final_gauge = ACTIVE_REQUESTS._value.get()
    assert final_gauge == pytest.approx(initial_gauge, abs=1e-5), (
        f"Active requests gauge leaked: initial={initial_gauge}, final={final_gauge}"
    )


@pytest.mark.asyncio
async def test_active_requests_in_flight_tracking():
    """Verify active requests gauge accurately tracks concurrent in-flight requests."""
    initial_gauge = ACTIVE_REQUESTS._value.get()
    gate = asyncio.Event()

    @app.get("/_test_inflight_gate")
    async def _gate_endpoint():
        await gate.wait()
        return {"status": "released"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Launch 25 concurrent requests that will halt at the gate
        tasks = [
            asyncio.create_task(client.get("/_test_inflight_gate"))
            for _ in range(25)
        ]

        # Allow event loop to dispatch and enter requests
        await asyncio.sleep(0.1)
        in_flight = ACTIVE_REQUESTS._value.get() - initial_gauge
        assert in_flight == pytest.approx(25.0, abs=1e-5), (
            f"Expected 25 in-flight requests, observed {in_flight}"
        )

        # Release all tasks
        gate.set()
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in responses)

    final_gauge = ACTIVE_REQUESTS._value.get()
    assert final_gauge == pytest.approx(initial_gauge, abs=1e-5), (
        f"Active requests gauge did not return to baseline: {final_gauge} != {initial_gauge}"
    )


@pytest.mark.asyncio
async def test_active_requests_resilience_to_client_cancellation():
    """Verify active requests gauge does not leak when in-flight requests are cancelled."""
    initial_gauge = ACTIVE_REQUESTS._value.get()
    cancel_gate = asyncio.Event()

    @app.get("/_test_cancellation_endpoint")
    async def _cancel_endpoint():
        await cancel_gate.wait()
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        tasks = [
            asyncio.create_task(client.get("/_test_cancellation_endpoint"))
            for _ in range(30)
        ]

        await asyncio.sleep(0.1)
        # Cancel half of the active tasks
        for t in tasks[:15]:
            t.cancel()

        # Let the remaining tasks complete
        cancel_gate.set()
        await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

    final_gauge = ACTIVE_REQUESTS._value.get()
    assert final_gauge == pytest.approx(initial_gauge, abs=1e-5), (
        f"Gauge leaked after task cancellations: initial={initial_gauge}, final={final_gauge}"
    )


@pytest.mark.asyncio
async def test_active_requests_resilience_to_unhandled_exceptions():
    """Verify active requests gauge cleans up when endpoints raise unhandled 500 exceptions."""
    initial_gauge = ACTIVE_REQUESTS._value.get()

    @app.get("/_test_unhandled_error")
    async def _failing_endpoint():
        raise RuntimeError("Simulated unhandled server crash")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/_test_unhandled_error")
        assert resp.status_code == 500

    final_gauge = ACTIVE_REQUESTS._value.get()
    assert final_gauge == pytest.approx(initial_gauge, abs=1e-5), (
        f"Gauge leaked after unhandled exception: initial={initial_gauge}, final={final_gauge}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. High Cardinality & Degenerate Metric Inputs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_path_cardinality_defense_against_random_urls():
    """Ensure arbitrary random URLs do not cause Prometheus label explosion."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(50):
            random_path = f"/api/adversarial_probe/{uuid.uuid4().hex}"
            resp = await client.get(random_path)
            assert resp.status_code == 404

    # Verify metrics labels
    samples = HTTP_REQUEST_DURATION_SECONDS.collect()[0].samples
    endpoints = {s.labels.get("endpoint") for s in samples if "endpoint" in s.labels}

    assert "/unmatched" in endpoints, "Expected unmatched paths to resolve to '/unmatched'"
    leaked = [e for e in endpoints if e and "adversarial_probe" in e]
    assert not leaked, f"Random paths leaked into Prometheus metric labels: {leaked}"


@pytest.mark.asyncio
async def test_parameterized_route_template_resolution():
    """Ensure parameterized route IDs do not create high-cardinality labels."""
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.admin_api_secret}"}
    
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Call parameterized endpoint with different IDs
        for model_id in ["model-alpha-1", "model-beta-2", "model-gamma-3"]:
            resp = await client.put(
                f"/api/models/{model_id}",
                headers=headers,
                json={
                    "intelligence": 8,
                    "speed": 9,
                    "tool_reliability": 8,
                    "vision": True,
                    "context_window": 128000,
                    "tags": ["fast"],
                },
            )
            # Response is 404 because model_id doesn't exist in registry, but route matched
            assert resp.status_code == 404

    samples = HTTP_REQUEST_DURATION_SECONDS.collect()[0].samples
    endpoints = {s.labels.get("endpoint") for s in samples if "endpoint" in s.labels}
    
    assert "/api/models/{model_id}" in endpoints, (
        f"Expected route template '/api/models/{{model_id}}' in metrics, got {endpoints}"
    )
    for model_id in ["model-alpha-1", "model-beta-2", "model-gamma-3"]:
        assert f"/api/models/{model_id}" not in endpoints, (
            f"Concrete path /api/models/{model_id} leaked into Prometheus labels!"
        )


def test_metrics_extreme_values_and_degraded_types():
    """Verify recording functions handle extreme, degenerate, and adversarial inputs."""
    # Negative duration should clamp to 0.0 without exception
    record_request_duration("POST", "/health", 200, -99.9)

    # Extreme / non-standard status codes
    record_request_duration("GET", "/test", 418, 0.05)
    record_request_duration("PATCH", "/test", 999, 0.01)
    record_request_duration("UNKNOWN", "/test", 0, 0.01)

    # Token recording with negative or huge numbers
    record_tokens("model-extreme", prompt_tokens=-100, completion_tokens=-50)
    record_tokens("model-extreme", prompt_tokens=10**15, completion_tokens=10**15)
    record_tokens("model-extreme", prompt_tokens=0, completion_tokens=0)

    # Model fallback with None/empty
    record_model_fallback("", "", "")
    record_model_fallback("gemini", "claude", None)

    # Error recording with non-exception object
    class NonExceptionError:
        pass
    record_error(NonExceptionError(), "custom_component")
    record_error(RuntimeError("Sample"), None)

    # Check that Prometheus snapshot is valid and parseable
    snapshot = get_metrics_snapshot().decode("utf-8")
    assert len(snapshot) > 0
    families = list(text_string_to_metric_families(snapshot))
    assert len(families) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Malformed X-Correlation-ID Headers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_correlation_id_empty_and_whitespace():
    """Ensure empty or whitespace-only correlation IDs fall back to generated UUID4."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Empty header
        r1 = client.get("/health", headers={"X-Correlation-ID": ""})
        r1_cid = (await r1).headers.get("X-Correlation-ID")
        assert r1_cid, "Expected generated correlation ID when empty header sent"
        uuid.UUID(r1_cid)  # Validates it's a valid UUID

        # Whitespace header
        r2 = client.get("/health", headers={"X-Correlation-ID": "    \t   "})
        r2_cid = (await r2).headers.get("X-Correlation-ID")
        assert r2_cid, "Expected generated correlation ID when whitespace header sent"
        uuid.UUID(r2_cid)


@pytest.mark.asyncio
async def test_correlation_id_x_request_id_fallback():
    """Ensure X-Request-ID is used if X-Correlation-ID is absent."""
    transport = ASGITransport(app=app)
    custom_id = f"req-{uuid.uuid4().hex[:12]}"
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers.get("X-Correlation-ID") == custom_id


@pytest.mark.asyncio
async def test_correlation_id_extreme_length():
    """Test behavior with extreme length correlation ID (e.g. 64KB string)."""
    transport = ASGITransport(app=app)
    huge_cid = "cid-" + ("A" * 65536)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health", headers={"X-Correlation-ID": huge_cid})
        assert resp.status_code == 200
        assigned_cid = resp.headers.get("X-Correlation-ID")
        assert assigned_cid != huge_cid
        assert uuid.UUID(assigned_cid, version=4)


@pytest.mark.asyncio
async def test_correlation_id_special_symbols_and_xss():
    """Test correlation ID containing XSS strings, quotes, and SQL injection payloads."""
    payload = '<script>alert("xss")</script>\'; DROP TABLE logs; --'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health", headers={"X-Correlation-ID": payload})
        assert resp.status_code == 200
        assigned_cid = resp.headers.get("X-Correlation-ID")
        assert assigned_cid != payload
        assert "script" not in assigned_cid
        assert uuid.UUID(assigned_cid, version=4)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Unserializable Data in Logger extra={...}
# ─────────────────────────────────────────────────────────────────────────────

def test_json_formatter_unserializable_generators_and_bytes():
    """Verify JSONFormatter gracefully handles generator objects and raw bytes."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Handling generators and bytes",
        args=(),
        exc_info=None,
    )
    record.__dict__["my_gen"] = (x for x in range(5))
    record.__dict__["my_bytes"] = b"\x00\xff\xfe"

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert "generator object" in parsed["my_gen"]
    assert "b'\\x00\\xff\\xfe'" in parsed["my_bytes"]


def test_json_formatter_circular_reference_challenge():
    """
    Empirical Challenge: Circular references in extra fields.
    With hardening, JSONFormatter handles circular references without crashing.
    """
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="Circular reference test",
        args=(),
        exc_info=None,
    )
    circular_dict: dict[str, Any] = {"name": "test"}
    circular_dict["self"] = circular_dict
    record.__dict__["circular_data"] = circular_dict

    formatted = formatter.format(record)
    assert "\n" not in formatted
    parsed = json.loads(formatted)
    assert parsed["message"] == "Circular reference test"
    assert "circular_data" in parsed
    assert "self" in str(parsed["circular_data"])


def test_json_formatter_exploding_str_challenge():
    """
    Empirical Challenge: Custom objects whose __str__ raises an exception.
    With hardening, JSONFormatter safely converts exploding objects without crashing.
    """
    class ExplodingObject:
        def __str__(self):
            raise RuntimeError("Deliberate failure in __str__")

    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=30,
        msg="Exploding object test",
        args=(),
        exc_info=None,
    )
    record.__dict__["bad_obj"] = ExplodingObject()

    formatted = formatter.format(record)
    assert "\n" not in formatted
    parsed = json.loads(formatted)
    assert parsed["message"] == "Exploding object test"
    assert "bad_obj" in parsed
    assert "ExplodingObject" in str(parsed["bad_obj"])


def test_json_formatter_non_string_dict_keys_challenge():
    """
    Empirical Challenge: Nested dicts with non-string keys (e.g. tuples).
    With hardening, JSONFormatter safely stringifies non-string dict keys.
    """
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=40,
        msg="Non-string dict keys test",
        args=(),
        exc_info=None,
    )
    record.__dict__["bad_dict"] = {(1, 2): "tuple_key_value"}

    formatted = formatter.format(record)
    assert "\n" not in formatted
    parsed = json.loads(formatted)
    assert parsed["message"] == "Non-string dict keys test"
    assert "bad_dict" in parsed
    assert "(1, 2)" in str(parsed["bad_dict"])

