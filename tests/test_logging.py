"""
OmniAgent Phase 7 Test Suite — Structured JSON Logging Verification (Requirement R3)
════════════════════════════════════════════════════════════════════════════════════
Covers Requirement R3:
  "Transition all system logging to JSON structured logging with correlation IDs."

Tiers Covered:
  - Tier 1: JSONFormatter validity, required schema fields, ISO-8601 UTC timestamp
  - Tier 2: Boundary formatting (escaped newlines, quotes, unicode, exc_info stack traces, extra dicts)
  - Tier 3: Asynchronous contextvars correlation ID propagation & concurrency task isolation
  - Tier 4: NDJSON stream ingestion & FastAPI CorrelationIdMiddleware response header verification
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import re
import sys
import time
import uuid
from typing import Any, Dict, Optional

import pytest
from starlette.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# Reference JSON Formatter & ContextVar Engine for Specification Verification
# ─────────────────────────────────────────────────────────────────────────────

correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


def set_correlation_id(cid: str) -> contextvars.Token:
    return correlation_id_ctx.set(cid)


def reset_correlation_id(token: contextvars.Token) -> None:
    correlation_id_ctx.reset(token)


class JSONFormatterReference(logging.Formatter):
    """
    Reference JSONFormatter implementing the Phase 7 specification:
      - Emits single-line valid JSON
      - ISO-8601 UTC timestamp
      - Includes correlation_id from contextvars
      - Merges extra attributes dynamically
    """

    def format(self, record: logging.LogRecord) -> str:
        # 1. UTC ISO-8601 Timestamp
        utc_time = time.gmtime(record.created)
        msecs = int(record.msecs)
        timestamp_str = f"{time.strftime('%Y-%m-%dT%H:%M:%S', utc_time)}.{msecs:03d}Z"

        # 2. Correlation ID
        cid = getattr(record, "correlation_id", None) or get_correlation_id() or ""

        # 3. Exception formatting
        exc_text = None
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)

        data: Dict[str, Any] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": cid,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "exception": exc_text,
        }

        # 4. Merge extra attributes
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "correlation_id"
        }
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                data[key] = val

        return json.dumps(data, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Feature Coverage (Schema & Format)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_json_formatter_produces_single_line_valid_json() -> None:
    """
    Tier 1: Every log record formatted by JSONFormatter must be a valid,
    single-line parseable JSON string.
    Derived from: ORIGINAL_REQUEST.md:56, Survey 3 §2.
    """
    formatter = JSONFormatterReference()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Task completed successfully | duration=0.12s",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)

    # Must be single line (no raw newlines)
    assert "\n" not in formatted, "JSON log output must be a single line"

    # Must parse cleanly as JSON
    data = json.loads(formatted)
    assert isinstance(data, dict)
    assert data["message"] == "Task completed successfully | duration=0.12s"
    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"


@pytest.mark.unit
def test_json_formatter_required_schema_fields() -> None:
    """
    Tier 1: Validates that all required schema fields are present:
      timestamp, level, logger, message, correlation_id, module, function, line, exception.
    """
    formatter = JSONFormatterReference()
    record = logging.LogRecord(
        name="omniagent.core",
        level=logging.WARNING,
        pathname="core/agent.py",
        lineno=88,
        msg="Fallback model engaged",
        args=(),
        exc_info=None,
    )
    data = json.loads(formatter.format(record))

    required_keys = [
        "timestamp", "level", "logger", "message",
        "correlation_id", "module", "function", "line", "exception"
    ]
    for k in required_keys:
        assert k in data, f"Required field '{k}' missing from structured log JSON"


@pytest.mark.unit
def test_json_formatter_iso8601_utc_timestamp_format() -> None:
    """
    Tier 1: Timestamps must adhere to ISO-8601 in UTC with 'Z' suffix:
      Format: YYYY-MM-DDTHH:MM:SS.mmmZ
    """
    formatter = JSONFormatterReference()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="t.py", lineno=1, msg="hi", args=(), exc_info=None
    )
    data = json.loads(formatter.format(record))
    ts = data["timestamp"]

    iso_regex = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
    assert re.match(iso_regex, ts), f"Timestamp '{ts}' does not match ISO-8601 UTC regex {iso_regex}"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Boundary & Formatting Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_json_formatter_escapes_newlines_quotes_and_unicode() -> None:
    """
    Tier 2: Extreme characters in log messages:
      Multiline text, quotes, escape sequences, and emojis must NOT break
      single-line NDJSON syntax.
    """
    formatter = JSONFormatterReference()
    complex_msg = 'Line 1\nLine 2\t"quoted"\r\nSpecial chars: \\ / \b \f \0 and emojis: 🤖🔥🚀'
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="t.py", lineno=1, msg=complex_msg, args=(), exc_info=None
    )
    formatted = formatter.format(record)

    # Must still be strictly 1 line
    assert "\n" not in formatted

    # Must deserialize without data loss
    data = json.loads(formatted)
    assert data["message"] == complex_msg


@pytest.mark.unit
def test_json_formatter_captures_exception_traceback() -> None:
    """
    Tier 2: Exception handling:
      When an exception is logged via exc_info, the exception string must be
      captured in the 'exception' field.
    """
    formatter = JSONFormatterReference()
    try:
        raise ValueError("Invalid configuration parameter: port out of range")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test", level=logging.CRITICAL, pathname="t.py", lineno=1,
        msg="Fatal startup exception", args=(), exc_info=exc_info
    )
    data = json.loads(formatter.format(record))

    assert data["exception"] is not None
    assert "ValueError: Invalid configuration parameter" in data["exception"]
    assert "Traceback (most recent call last)" in data["exception"]


@pytest.mark.unit
def test_json_formatter_dynamic_extra_fields() -> None:
    """
    Tier 2: Extra attributes:
      Arbitrary key-value pairs passed in extra={} must be merged into the JSON root.
    """
    formatter = JSONFormatterReference()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="t.py", lineno=1,
        msg="Token consumption", args=(), exc_info=None
    )
    record.__dict__["model"] = "gpt-4o"
    record.__dict__["tokens"] = 1250
    record.__dict__["cost_usd"] = 0.035

    data = json.loads(formatter.format(record))
    assert data["model"] == "gpt-4o"
    assert data["tokens"] == 1250
    assert data["cost_usd"] == 0.035


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: ContextVar Correlation ID Propagation & Task Isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_correlation_id_injected_from_contextvar() -> None:
    """
    Tier 3: ContextVar extraction:
      When correlation_id_ctx is set, formatter automatically attaches it
      to the log record.
    """
    formatter = JSONFormatterReference()
    test_cid = f"corr-{uuid.uuid4().hex[:8]}"

    token = set_correlation_id(test_cid)
    try:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="t.py", lineno=1,
            msg="Processing inbound request", args=(), exc_info=None
        )
        data = json.loads(formatter.format(record))
        assert data["correlation_id"] == test_cid
    finally:
        reset_correlation_id(token)


@pytest.mark.unit
async def test_correlation_id_concurrency_task_isolation() -> None:
    """
    Tier 3: Async Task Isolation:
      Verifies that two concurrently executing coroutines maintain their own
      isolated correlation IDs without cross-contamination.
    """
    formatter = JSONFormatterReference()

    async def worker(worker_id: str, cid: str) -> list[str]:
        token = set_correlation_id(cid)
        logs = []
        try:
            for step in range(3):
                await asyncio.sleep(0.01)
                rec = logging.LogRecord(
                    name=f"worker_{worker_id}", level=logging.INFO, pathname="t.py", lineno=1,
                    msg=f"Step {step}", args=(), exc_info=None
                )
                entry = json.loads(formatter.format(rec))
                logs.append(entry["correlation_id"])
        finally:
            reset_correlation_id(token)
        return logs

    cid_a = "cid-task-A-1111"
    cid_b = "cid-task-B-2222"

    results_a, results_b = await asyncio.gather(
        worker("A", cid_a),
        worker("B", cid_b),
    )

    assert all(c == cid_a for c in results_a), f"Task A logs leaked: {results_a}"
    assert all(c == cid_b for c in results_b), f"Task B logs leaked: {results_b}"


@pytest.mark.unit
def test_admin_api_correlation_id_middleware() -> None:
    """
    Tier 3: Inbound HTTP Request Header Propagation:
      FastAPI CorrelationIdMiddleware must:
        1. Extract incoming `X-Correlation-ID` or `X-Request-ID`
        2. Set it in contextvars during request processing
        3. Mirror it in the outgoing HTTP response header `X-Correlation-ID`
      Derived from: PROJECT.md:54, Survey 3 §2.
    """
    from adapters.admin_api import app

    client = TestClient(app, raise_server_exceptions=False)
    custom_cid = "trace-e2e-abc-123"

    resp = client.get("/api/health", headers={"X-Correlation-ID": custom_cid})
    assert resp.status_code == 200

    out_cid = resp.headers.get("x-correlation-id") or resp.headers.get("X-Correlation-ID")
    if not out_cid:
        pytest.fail(
            "IMPLEMENTATION GAP (M1): X-Correlation-ID missing from HTTP response headers. "
            "CorrelationIdMiddleware must propagate and return X-Correlation-ID in adapters/admin_api.py"
        )
    assert out_cid == custom_cid


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4: Real-World NDJSON Stream Ingestion
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ndjson_log_stream_ingestion() -> None:
    """
    Tier 4: Production Log Aggregation Simulation:
      Simulates an external log collector (e.g. Promtail, Vector, or FluentBit)
      streaming and parsing NDJSON lines emitted by JSONFormatter.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatterReference())

    test_logger = logging.getLogger("ndjson_test")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)

    # Emit 10 log messages with varying payloads
    for i in range(10):
        test_logger.info(f"Log event #{i}", extra={"event_id": i, "status": "ok"})

    handler.flush()
    lines = stream.getvalue().strip().splitlines()

    assert len(lines) == 10
    for idx, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["event_id"] == idx
        assert parsed["status"] == "ok"
        assert parsed["level"] == "INFO"


# ─────────────────────────────────────────────────────────────────────────────
# In-Tree core.logger Module Direct Verification
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_real_json_formatter_basic_format() -> None:
    """Verify core.logger.JSONFormatter produces valid single-line JSON."""
    from core.logger import JSONFormatter
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_real_basic_json")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    logger.info("Direct test of core.logger.JSONFormatter")

    output = stream.getvalue().strip()
    assert "\n" not in output, "Log record must be strictly a single line"

    record = json.loads(output)
    assert record["level"] == "INFO"
    assert record["logger"] == "test_real_basic_json"
    assert record["message"] == "Direct test of core.logger.JSONFormatter"
    assert record["timestamp"].endswith("Z")
    assert "T" in record["timestamp"]
    assert record["exception"] is None


@pytest.mark.unit
def test_real_correlation_id_scope_and_contextvars() -> None:
    """Verify correlation_id_scope sets and restores contextvar."""
    from core.logger import (
        correlation_id_scope,
        get_correlation_id,
        set_correlation_id,
        reset_correlation_id,
    )
    assert get_correlation_id() is None

    with correlation_id_scope("test-scoped-cid") as cid:
        assert cid == "test-scoped-cid"
        assert get_correlation_id() == "test-scoped-cid"

    assert get_correlation_id() is None

    # Test manual set/reset
    tok = set_correlation_id("manual-cid")
    assert get_correlation_id() == "manual-cid"
    reset_correlation_id(tok)
    assert get_correlation_id() is None


@pytest.mark.unit
def test_real_json_formatter_extra_fields() -> None:
    """Verify extra attributes are serialized into top-level JSON fields."""
    from core.logger import JSONFormatter
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_real_extra_json")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    logger.info("Extra fields test", extra={"user_id": "u42", "model": "gpt-4o", "tokens": 500})
    output = stream.getvalue().strip()
    data = json.loads(output)
    assert data["user_id"] == "u42"
    assert data["model"] == "gpt-4o"
    assert data["tokens"] == 500


@pytest.mark.unit
def test_real_json_formatter_exception_traceback() -> None:
    """Verify exception tracebacks are safely JSON-escaped on a single line."""
    from core.logger import JSONFormatter
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_real_exc_json")
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)

    try:
        raise ValueError("Critical simulated error\nwith newline")
    except ValueError:
        logger.error("Exception occurred", exc_info=True)

    output = stream.getvalue().strip()
    assert "\n" not in output, "Exception log record must remain strictly on one line"
    data = json.loads(output)
    assert data["level"] == "ERROR"
    assert data["exception"] is not None
    assert "ValueError: Critical simulated error" in data["exception"]


@pytest.mark.unit
def test_admin_api_correlation_middleware_fallback_request_id() -> None:
    """Verify Admin API middleware accepts X-Request-ID when X-Correlation-ID is absent."""
    from adapters.admin_api import app
    client = TestClient(app)
    custom_id = "request-id-fallback-888"

    response = client.get("/api/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_id


@pytest.mark.unit
def test_admin_api_correlation_middleware_generated_uuid() -> None:
    """Verify Admin API generates a valid UUID4 when no correlation header is provided."""
    from adapters.admin_api import app
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    cid = response.headers.get("X-Correlation-ID")
    assert cid is not None
    assert len(cid) == 36
    # Verify valid UUID4
    parsed = uuid.UUID(cid, version=4)
    assert str(parsed) == cid


@pytest.mark.unit
def test_admin_api_correlation_middleware_context_cleanup() -> None:
    """Verify correlation ID context is clean outside requests."""
    from adapters.admin_api import app
    from core.logger import get_correlation_id
    client = TestClient(app)
    response = client.get("/api/health", headers={"X-Correlation-ID": "temp-cleanup-cid"})
    assert response.status_code == 200
    assert get_correlation_id() is None


@pytest.mark.unit
def test_admin_api_cors_exposes_correlation_id() -> None:
    """Verify CORS middleware exposes X-Correlation-ID and X-Request-ID headers."""
    from adapters.admin_api import app
    client = TestClient(app)
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Correlation-ID",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-correlation-id" in allow_headers




# ─────────────────────────────────────────────────────────────────────────────
# Robustness Edge Cases: Circular References, Serialization Fallbacks
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_json_formatter_circular_reference_in_extra() -> None:
    """
    Tier 2: Circular reference handling in extra dict:
      Ensures circular references passed in extra={} do not crash json.dumps
      with ValueError('Circular reference detected') and are safely stringified.
    """
    from core.logger import JSONFormatter
    formatter = JSONFormatter()
    d: dict[str, Any] = {"name": "circular_dict"}
    d["self"] = d
    record = logging.LogRecord("test", logging.INFO, "t.py", 1, "Circular test", (), None)
    record.__dict__["cycle"] = d
    record.__dict__["safe_field"] = 12345

    output = formatter.format(record)
    assert "\n" not in output
    data = json.loads(output)
    assert data["message"] == "Circular test"
    assert data["safe_field"] == 12345
    assert "cycle" in data
    assert "self" in str(data["cycle"])


@pytest.mark.unit
def test_json_formatter_circular_list_in_extra() -> None:
    """
    Tier 2: Circular reference in extra list:
      Ensures circular lists in extra={} are safely converted without recursion.
    """
    from core.logger import JSONFormatter
    formatter = JSONFormatter()
    l: list[Any] = [1, 2]
    l.append(l)
    record = logging.LogRecord("test", logging.INFO, "t.py", 1, "List cycle", (), None)
    record.__dict__["cycle_list"] = l

    output = formatter.format(record)
    assert "\n" not in output
    data = json.loads(output)
    assert data["message"] == "List cycle"
    assert "cycle_list" in data
    assert "[...]" in str(data["cycle_list"])


@pytest.mark.unit
def test_json_formatter_broken_str_and_repr_in_extra() -> None:
    """
    Tier 2: Broken __str__ and __repr__ in extra objects:
      Ensures objects that raise exceptions during stringification do not crash the logger.
    """
    from core.logger import JSONFormatter
    formatter = JSONFormatter()

    class Explosive:
        def __str__(self) -> str:
            raise RuntimeError("Explosion in __str__")
        def __repr__(self) -> str:
            raise RuntimeError("Explosion in __repr__")

    record = logging.LogRecord("test", logging.WARNING, "t.py", 1, "Explosion test", (), None)
    record.__dict__["bad_obj"] = Explosive()
    record.__dict__["valid_key"] = "preserved"

    output = formatter.format(record)
    assert "\n" not in output
    data = json.loads(output)
    assert data["message"] == "Explosion test"
    assert data["valid_key"] == "preserved"
    assert "bad_obj" in data
    assert "Explosive" in data["bad_obj"]


@pytest.mark.unit
def test_json_formatter_non_string_dict_keys_in_extra() -> None:
    """
    Tier 2: Non-string dictionary keys in extra:
      Ensures dictionaries with tuple/set keys do not raise TypeError in json.dumps.
    """
    from core.logger import JSONFormatter
    formatter = JSONFormatter()
    record = logging.LogRecord("test", logging.INFO, "t.py", 1, "Tuple key test", (), None)
    record.__dict__["tuple_key_dict"] = {(1, 2): "val"}

    output = formatter.format(record)
    assert "\n" not in output
    data = json.loads(output)
    assert "tuple_key_dict" in data
    assert "(1, 2)" in str(data["tuple_key_dict"])


@pytest.mark.unit
def test_json_formatter_broken_record_msg() -> None:
    """
    Tier 2: Broken record.msg:
      Ensures records with un-stringifiable message objects fall back gracefully.
    """
    from core.logger import JSONFormatter
    formatter = JSONFormatter()

    class BadMsg:
        def __str__(self) -> str:
            raise RuntimeError("Failed to stringify message")

    record = logging.LogRecord("test", logging.ERROR, "t.py", 1, BadMsg(), (), None)
    output = formatter.format(record)
    assert "\n" not in output
    data = json.loads(output)
    assert "BadMsg" in data["message"]


# ─────────────────────────────────────────────────────────────────────────────
# Regression Suite: Dual-Layer Sensitive Query Parameter Redaction & Sanitization
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "param_name,expected_sensitive",
    [
        # Required nested structures
        ("auth[key]", True),
        ("user.key", True),
        ("config[auth]", True),
        ("config:auth", True),
        ("credentials[key]", True),
        ("credentials/key", True),
        ("headers[authorization]", True),
        # Extended nested & delimited variations
        ("nested[auth_key]", True),
        ("nested[key]", True),
        ("api[key]", True),
        ("auth:key", True),
        ("user/key", True),
        ("auth.key", True),
        ("system.auth.token", True),
        # Case-insensitivity variations
        ("AUTH[KEY]", True),
        ("User.Key", True),
        ("CONFIG[AUTH]", True),
        ("CREDENTIALS[KEY]", True),
        ("HEADERS[AUTHORIZATION]", True),
        # CamelCase & PascalCase sensitive parameters
        ("authKey", True),
        ("privateKey", True),
        ("accessKey", True),
        ("secretKey", True),
        ("apiKey", True),
        ("AuthKey", True),
        ("PrivateKey", True),
        # Sensitive credentials, signatures, and codes
        ("signature", True),
        ("sig", True),
        ("request_signature", True),
        ("passwd", True),
        ("pwd", True),
        ("passcode", True),
        ("credential", True),
        ("credentials", True),
        ("bearer", True),
        ("db_passwd", True),
        ("clientsecret", True),
        ("userpassword", True),
        # Safe operational query parameters (false-positive prevention)
        ("page", False),
        ("limit", False),
        ("offset", False),
        ("count", False),
        ("cursor", False),
        ("sort_key", False),
        ("sort-key", False),
        ("sortKey", False),
        ("SortKey", False),
        ("sortkey", False),
        ("primary_key", False),
        ("primaryKey", False),
        ("cache_key", False),
        ("cacheKey", False),
        ("foreign_key", False),
        ("routing_key", False),
        ("id_key", False),
        ("partition_key", False),
        ("max_tokens", False),
        ("prompt_tokens", False),
        ("completion_tokens", False),
        ("total_tokens", False),
        ("token_count", False),
        ("author", False),
        ("author_id", False),
        ("monkey", False),
        ("turkey", False),
        ("keyboard", False),
        ("secretary", False),
        ("filter[status]", False),
        ("user[name]", False),
        ("query", False),
        ("safe", False),
        ("sort_by", False),
        ("max_results", False),
        ("public_key", False),
        ("nested[max_tokens]", False),
        ("filter[sort_key]", False),
    ],
)
def test_is_sensitive_param_name_nested_delimiters(param_name: str, expected_sensitive: bool) -> None:
    """
    Regression Test: Verify _is_sensitive_param_name detects sensitive keywords
    across arbitrary non-alphanumeric delimiters ([], ., :, /) and camelCase without
    false positives on safe operational parameters.
    """
    from adapters.admin_api import _is_sensitive_param_name

    assert _is_sensitive_param_name(param_name) is expected_sensitive, (
        f"Parameter name '{param_name}' evaluated to {not expected_sensitive}, expected {expected_sensitive}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_param,expected_sanitized,secret_value",
    [
        ("auth[key]=foo", "auth[key]=[REDACTED]", "foo"),
        ("user.key=bar", "user.key=[REDACTED]", "bar"),
        ("config[auth]=baz", "config[auth]=[REDACTED]", "baz"),
        ("config:auth=baz", "config:auth=[REDACTED]", "baz"),
        ("credentials[key]=xyz", "credentials[key]=[REDACTED]", "xyz"),
        ("credentials/key=xyz", "credentials/key=[REDACTED]", "xyz"),
        ("headers[authorization]=bearer_token", "headers[authorization]=[REDACTED]", "bearer_token"),
        ("authKey=myauthkey", "authKey=[REDACTED]", "myauthkey"),
        ("privateKey=myprivatekey", "privateKey=[REDACTED]", "myprivatekey"),
        ("signature=sig123", "signature=[REDACTED]", "sig123"),
        ("passwd=pwd123", "passwd=[REDACTED]", "pwd123"),
    ],
)
def test_sanitize_query_params_nested_required_cases(
    raw_param: str,
    expected_sanitized: str,
    secret_value: str,
) -> None:
    """
    Regression Test: Verify _sanitize_query_params redacts values for nested sensitive
    parameter patterns, camelCase, and credential terms, preventing plaintext leakage.
    """
    from starlette.datastructures import QueryParams
    from adapters.admin_api import _sanitize_query_params

    qp = QueryParams(raw_param)
    sanitized = _sanitize_query_params(qp)

    assert expected_sanitized in sanitized
    assert secret_value not in sanitized


@pytest.mark.unit
def test_sanitize_query_params_safe_param_preservation() -> None:
    """
    Regression Test: Verify operational query parameters (sort_key, max_tokens, page, etc.)
    are completely preserved and never falsely redacted.
    """
    from starlette.datastructures import QueryParams
    from adapters.admin_api import _sanitize_query_params

    raw = (
        "sort_key=asc&max_tokens=100&page=1&limit=50&offset=10"
        "&sortKey=desc&secretary=true&monkey=yes&turkey=ok&keyboard=usb"
        "&author=alice&author_id=42&filter[sort_key]=asc&data[max_tokens]=500"
    )
    qp = QueryParams(raw)
    sanitized = _sanitize_query_params(qp)

    assert "sort_key=asc" in sanitized
    assert "max_tokens=100" in sanitized
    assert "page=1" in sanitized
    assert "limit=50" in sanitized
    assert "offset=10" in sanitized
    assert "sortKey=desc" in sanitized
    assert "secretary=true" in sanitized
    assert "monkey=yes" in sanitized
    assert "turkey=ok" in sanitized
    assert "keyboard=usb" in sanitized
    assert "author=alice" in sanitized
    assert "author_id=42" in sanitized
    assert "filter[sort_key]=asc" in sanitized
    assert "data[max_tokens]=500" in sanitized
    assert "[REDACTED]" not in sanitized


@pytest.mark.unit
def test_sanitize_query_params_mixed_nested_and_safe() -> None:
    """
    Regression Test: Verify mixed query string containing both safe and nested sensitive
    parameters redacts all credentials while preserving safe operational arguments.
    """
    from starlette.datastructures import QueryParams
    from adapters.admin_api import _sanitize_query_params

    raw = (
        "page=1&auth[key]=foo&limit=50&user.key=bar&config:auth=baz"
        "&safe=true&credentials/key=xyz&headers[authorization]=bearer_token"
        "&sort_key=asc&max_tokens=100&authKey=myauth&privateKey=mypriv"
    )
    qp = QueryParams(raw)
    sanitized = _sanitize_query_params(qp)

    # Safe parameters preserved
    assert "page=1" in sanitized
    assert "limit=50" in sanitized
    assert "safe=true" in sanitized
    assert "sort_key=asc" in sanitized
    assert "max_tokens=100" in sanitized

    # All sensitive keys redacted
    assert "auth[key]=[REDACTED]" in sanitized
    assert "user.key=[REDACTED]" in sanitized
    assert "config:auth=[REDACTED]" in sanitized
    assert "credentials/key=[REDACTED]" in sanitized
    assert "headers[authorization]=[REDACTED]" in sanitized
    assert "authKey=[REDACTED]" in sanitized
    assert "privateKey=[REDACTED]" in sanitized

    # All sensitive values stripped
    for secret in ["foo", "bar", "baz", "xyz", "bearer_token", "myauth", "mypriv"]:
        assert secret not in sanitized, f"Secret '{secret}' was not redacted!"


@pytest.mark.unit
def test_sanitize_query_params_url_encoded_nested() -> None:
    """
    Regression Test: Verify URL-encoded nested parameter keys are properly decoded
    and redacted without escaping or parsing failures.
    """
    from starlette.datastructures import QueryParams
    from adapters.admin_api import _sanitize_query_params

    qp = QueryParams(
        "auth%5Bkey%5D=foo&user%2Ekey=bar&config%5Bauth%5D=baz"
        "&credentials%5Bkey%5D=xyz&headers%5Bauthorization%5D=bearer_token"
    )
    sanitized = _sanitize_query_params(qp)

    for secret in ["foo", "bar", "baz", "xyz", "bearer_token"]:
        assert secret not in sanitized, f"Secret '{secret}' leaked in URL-encoded query!"

    assert "auth[key]=[REDACTED]" in sanitized
    assert "user.key=[REDACTED]" in sanitized
    assert "config[auth]=[REDACTED]" in sanitized
    assert "credentials[key]=[REDACTED]" in sanitized
    assert "headers[authorization]=[REDACTED]" in sanitized


@pytest.mark.unit
def test_admin_api_http_request_nested_query_param_redaction() -> None:
    """
    End-to-End Regression Test: Verify CorrelationIdMiddleware in adapters/admin_api
    logs HTTP requests with nested sensitive query parameters fully redacted in NDJSON output,
    while preserving safe operational query parameters.
    """
    from starlette.testclient import TestClient
    from adapters.admin_api import app
    from core.logger import JSONFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    log = logging.getLogger("adapters.admin_api")
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    try:
        client = TestClient(app)
        query = (
            "auth[key]=foo&user.key=bar&config:auth=baz"
            "&credentials/key=xyz&headers[authorization]=bearer_token"
            "&authKey=myauth&privateKey=mypriv&page=1&sort_key=asc&max_tokens=100"
        )
        response = client.get(f"/api/health?{query}")
        assert response.status_code == 200

        handler.flush()
        log_output = stream.getvalue()

        # Ensure raw secrets never appear anywhere in the log stream
        for secret in ["foo", "bar", "baz", "xyz", "bearer_token", "myauth", "mypriv"]:
            assert secret not in log_output, f"Raw secret '{secret}' leaked in Admin API HTTP log!"

        # Locate the "HTTP request started" log line and parse JSON
        started_records = [
            json.loads(line)
            for line in log_output.strip().splitlines()
            if "HTTP request started" in line
        ]
        assert len(started_records) >= 1, "Expected 'HTTP request started' log record not found"

        record = started_records[0]
        qp_logged = record.get("query_params", "")

        assert "auth[key]=[REDACTED]" in qp_logged
        assert "user.key=[REDACTED]" in qp_logged
        assert "config:auth=[REDACTED]" in qp_logged
        assert "credentials/key=[REDACTED]" in qp_logged
        assert "headers[authorization]=[REDACTED]" in qp_logged
        assert "authKey=[REDACTED]" in qp_logged
        assert "privateKey=[REDACTED]" in qp_logged
        assert "page=1" in qp_logged
        assert "sort_key=asc" in qp_logged
        assert "max_tokens=100" in qp_logged
    finally:
        log.removeHandler(handler)


