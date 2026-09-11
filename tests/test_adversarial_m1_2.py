"""
OmniAgent Phase 7 — Milestone 1 Challenger 2 Adversarial Stress Suite
═════════════════════════════════════════════════════════════════════
Adversarial challenge for:
1. Rapid reload / uncache re-import of `core/metrics.py` (idempotency, zero duplicate timeseries).
2. Concurrency stress testing of Prometheus `/metrics` scraping under heavy logging load.
3. Strict Prometheus exposition format 0.0.4 conformance via official parser.
4. Stress parsing of 2,000+ log lines from `JSONFormatter` under high concurrency.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import io
import json
import logging
import random
import re
import sys
import threading
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

import core.metrics as m
from adapters.admin_api import app
from core.logger import JSONFormatter, correlation_id_scope, get_logger


@pytest.mark.unit
def test_adversarial_metrics_rapid_reload_and_reimport() -> None:
    """
    Adversarial Challenge: Rapid reload & uncache re-import of core.metrics.
    Stresses Prometheus registration idempotency and ensures no
    `ValueError: Duplicated timeseries` occurs over 100 sequential reloads,
    20 sys.modules uncache/re-imports, and 5 concurrent worker threads.
    """
    # 1. Pre-populate metrics
    m.record_request_duration("GET", "/health", 200, 0.04)
    m.record_tokens("gemini-2.5-pro", 250, 100)
    m.record_model_fallback("gemini-2.5-pro", "claude-3-5-sonnet", "overloaded")
    m.record_error("TimeoutError", "model_router")
    m.inc_active_requests()
    m.record_llm_duration("google", "gemini-2.5-pro", 0.85)

    # 2. Sequential rapid reload (100 iterations)
    for i in range(100):
        try:
            curr_m = sys.modules["core.metrics"]
            importlib.reload(curr_m)
            curr_m.record_request_duration("GET", f"/api/route_{i % 5}", 200, 0.01)
            curr_m.record_tokens("test-model", 5, 5)
        except Exception as exc:
            pytest.fail(f"Sequential reload failed at iteration {i} with error: {exc}")

    # 3. Uncache and re-import (20 iterations)
    for i in range(20):
        try:
            if "core.metrics" in sys.modules:
                del sys.modules["core.metrics"]
            reimported_m = importlib.import_module("core.metrics")
            reimported_m.record_error("SimulatedError", "reload_test")
            snap = reimported_m.get_metrics_snapshot()
            assert b"omniagent_errors_total" in snap
        except Exception as exc:
            pytest.fail(f"Uncache re-import failed at iteration {i} with error: {exc}")

    # 4. Multi-threaded concurrent reload and scraping
    errors: list[tuple[str, Exception]] = []

    def reloader_task() -> None:
        for _ in range(30):
            try:
                mod = sys.modules.get("core.metrics")
                if mod:
                    importlib.reload(mod)
                time.sleep(0.001)
            except Exception as e:
                errors.append(("reloader", e))

    def recorder_task() -> None:
        for _ in range(50):
            try:
                mod = sys.modules.get("core.metrics", m)
                mod.record_request_duration("POST", "/api/data", 201, 0.02)
                mod.record_tokens("concurrent-model", 10, 10)
                mod.record_model_fallback("m1", "m2", "rate_limit")
                time.sleep(0.001)
            except Exception as e:
                errors.append(("recorder", e))

    def scraper_task() -> None:
        for _ in range(30):
            try:
                mod = sys.modules.get("core.metrics", m)
                snapshot = mod.get_metrics_snapshot()
                assert len(snapshot) > 0
                time.sleep(0.001)
            except Exception as e:
                errors.append(("scraper", e))

    threads = [
        threading.Thread(target=reloader_task),
        threading.Thread(target=reloader_task),
        threading.Thread(target=recorder_task),
        threading.Thread(target=recorder_task),
        threading.Thread(target=scraper_task),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent reload/record/scrape produced errors: {errors}"


@pytest.mark.unit
def test_adversarial_json_log_stream_2000_lines() -> None:
    """
    Adversarial Challenge: JSONFormatter integrity across 2,000+ log lines.
    Tests multi-threaded concurrent logging with adversarial payloads:
    - Escaped newlines, tabs, quotes, backslashes, null bytes
    - Multiline stack traces and chained exceptions
    - Large payloads (5KB strings)
    - Unicode scripts (Chinese, Japanese, Arabic, Russian, Hebrew, Hindi) and emojis
    - Non-string records, nested dictionaries, and custom objects
    Verifies that 100% of emitted lines:
    1. Are strictly single-line strings (no raw newlines)
    2. Are parseable by json.loads without JSONDecodeError
    3. Conform to the mandatory schema (all 9 required fields)
    4. Have valid ISO-8601 UTC millisecond timestamps ending in 'Z'
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = JSONFormatter()
    handler.setFormatter(formatter)

    logger = logging.getLogger("adversarial_stress_logger_c2")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    num_threads = 10
    lines_per_thread = 200
    total_expected = num_threads * lines_per_thread

    def worker(worker_id: int) -> None:
        cid = f"adversarial-cid-{worker_id}-{uuid.uuid4().hex[:8]}"
        with correlation_id_scope(cid):
            for i in range(lines_per_thread):
                extra_payload: dict[str, Any] = {
                    "worker_id": worker_id,
                    "iteration": i,
                    "custom_num": 42.5,
                    "custom_set": {1, 2, 3},  # tests default=str
                    "custom_bytes": b"binary_test",
                }
                mod = i % 10
                if mod == 0:
                    logger.info(
                        f"Iter {i}: Escaped newline\ncarriage return\r\ntab\tquote\"slash\\null\x00",
                        extra=extra_payload,
                    )
                elif mod == 1:
                    logger.warning(
                        f"Iter {i}: Unicode: 日本語 简体中文 Русский العربية हिन्दी 🚀🔥⚡️🎉",
                        extra=extra_payload,
                    )
                elif mod == 2:
                    try:
                        raise RuntimeError(f"Simulated error in worker {worker_id} iter {i}\nmultiline detail")
                    except RuntimeError:
                        logger.error(f"Iter {i}: Caught exception", exc_info=True, extra=extra_payload)
                elif mod == 3:
                    logger.debug(f"Iter {i}: Large payload " + ("X" * 5000), extra=extra_payload)
                elif mod == 4:
                    logger.info("", extra=extra_payload)
                elif mod == 5:
                    logger.info({"complex_msg": True, "count": i}, extra=extra_payload)
                elif mod == 6:
                    try:
                        try:
                            raise ValueError("Root cause")
                        except ValueError as ve:
                            raise RuntimeError("Chained failure") from ve
                    except RuntimeError:
                        logger.critical(f"Iter {i}: Chained exception", exc_info=True, extra=extra_payload)
                elif mod == 7:
                    extra_payload["nested"] = {"a": {"b": [1, 2, {"c": "deep"}]}}
                    logger.info(f"Iter {i}: Nested structure", extra=extra_payload)
                elif mod == 8:
                    extra_payload["correlation_id"] = f"override-cid-{worker_id}-{i}"
                    logger.info(f"Iter {i}: Overridden correlation ID", extra=extra_payload)
                else:
                    logger.info(f"Iter {i}: Standard message", extra=extra_payload)

    threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    handler.flush()
    lines = stream.getvalue().splitlines()

    assert len(lines) == total_expected, f"Expected {total_expected} lines, got {len(lines)}"

    iso_regex = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    required_keys = [
        "timestamp", "level", "logger", "message",
        "correlation_id", "module", "function", "line", "exception"
    ]

    for idx, line in enumerate(lines):
        assert "\n" not in line and "\r" not in line, f"Line {idx} contains raw newline"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Line {idx} failed JSON parsing: {exc}\nContent: {line[:200]}")

        for k in required_keys:
            assert k in record, f"Line {idx} missing required field '{k}'"

        assert iso_regex.match(record["timestamp"]), f"Line {idx} timestamp invalid: {record['timestamp']}"
        assert record["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@pytest.mark.unit
def test_adversarial_prometheus_exposition_format_parser() -> None:
    """
    Adversarial Challenge: Validate /metrics plain-text output against the official
    Prometheus exposition parser (text_string_to_metric_families).
    Verifies 0 parser errors and presence of all 5 mandatory metric families.
    """
    client = TestClient(app)

    # Populate multiple labels and edge-case values
    m.record_request_duration("GET", "/api/adversarial/test?q=1&p=2", 200, 0.123)
    m.record_request_duration("POST", "/api/adversarial/test", 500, 1.456)
    m.record_tokens("custom-llm-model", prompt_tokens=300, completion_tokens=150)
    m.record_model_fallback("model-a", "model-b", "circuit_breaker_open")
    m.record_error("DeadlockError", "concurrency_controller")
    m.inc_active_requests()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")

    # Parse using official Prometheus client parser
    families = list(text_string_to_metric_families(resp.text))
    assert len(families) > 0, "Parser returned 0 metric families"

    family_names = {f.name for f in families}

    required_families = [
        "omniagent_http_request_duration_seconds",
        "http_request_duration_seconds",
        "omniagent_token_usage",
        "omniagent_model_fallbacks",
        "omniagent_errors",
        "omniagent_active_requests",
    ]

    for expected in required_families:
        assert any(expected in name for name in family_names), (
            f"Expected metric family '{expected}' not found in parsed families: {family_names}"
        )


@pytest.mark.unit
def test_adversarial_concurrent_scraping_and_logging_throughput() -> None:
    """
    Adversarial Challenge: High-throughput concurrent scraping, logging,
    metric updates, and module reloading without deadlocks or corrupted outputs.
    """
    client = TestClient(app)
    logger = get_logger("throughput_adversarial_c2")

    errors: list[str] = []

    def scraper_loop() -> None:
        for _ in range(20):
            try:
                resp = client.get("/metrics")
                assert resp.status_code == 200
                parsed = list(text_string_to_metric_families(resp.text))
                assert len(parsed) > 0
            except Exception as exc:
                errors.append(f"Scraper error: {exc}")

    def api_loop() -> None:
        for j in range(30):
            try:
                cid = f"test-throughput-cid-{j}"
                resp = client.get("/api/health", headers={"X-Correlation-ID": cid})
                assert resp.status_code == 200
                assert resp.headers.get("X-Correlation-ID") == cid
            except Exception as exc:
                errors.append(f"API client error: {exc}")

    def metric_loop() -> None:
        for _ in range(50):
            try:
                m.record_request_duration("GET", "/api/health", 200, 0.005)
                m.record_tokens("test-model", 50, 25)
                m.record_model_fallback("a", "b", "reason")
                m.record_error("SampleError", "unit")
            except Exception as exc:
                errors.append(f"Metric recording error: {exc}")

    def logging_loop() -> None:
        for k in range(50):
            try:
                logger.info(f"Throughput event {k}", extra={"k": k})
            except Exception as exc:
                errors.append(f"Logging error: {exc}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(scraper_loop),
            pool.submit(scraper_loop),
            pool.submit(api_loop),
            pool.submit(api_loop),
            pool.submit(metric_loop),
            pool.submit(metric_loop),
            pool.submit(logging_loop),
            pool.submit(logging_loop),
        ]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Errors occurred during concurrent throughput stress: {errors}"
