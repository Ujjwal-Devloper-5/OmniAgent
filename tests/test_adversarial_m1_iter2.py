"""
OmniAgent Phase 7 — Milestone 1 Iteration 2 Adversarial Stress Suite
═════════════════════════════════════════════════════════════════════
Empirical challenge suite for:
1. Circular references in logging:
   - Self-referencing dict `a = {}; a["self"] = a` in `logger.info("msg", extra={"self": a})`
   - Mutual circular dicts
   - Circular lists
   - Deeply nested circular references
   - Circular dict in `logger.info(a)` (msg itself)
   - Recursive __str__ and __repr__ infinite loops
2. Hostile `X-Correlation-ID` header injection:
   - CRLF and newlines (`evil\\r\\nX-Injected: attack`, `evil\\nbar`)
   - Null bytes (`evil\\x00cid`, `\\x00evil`, `evil\\x00`)
   - Length limits (500 chars, 65 chars rejected, 64 chars accepted)
   - Surrogates, emojis, XSS payloads, SQLi, shell commands, path traversal
3. Query parameter redaction & injection:
   - URL-encoded parameter names and values
   - Nested structures with brackets (`auth[token]`, `user[password]`, `user[key]`, `auth[key]`, `config[auth]`)
   - Dot notation (`user.key`, `auth.key`)
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from typing import Any
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from adapters.admin_api import (
    app,
    _is_sensitive_param_name,
    _sanitize_query_params,
    _validate_or_generate_correlation_id,
)
from core.logger import JSONFormatter, get_logger


# ─────────────────────────────────────────────────────────────────────────────
# 1. Circular Reference Stress Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_circular_reference_extra_dict_self() -> None:
    """
    Empirical Challenge 1: Self-referencing dictionary in logger.info extra.
    logger.info("msg", extra={"self": a}) where a = {}; a["self"] = a.
    Verifies no exception is raised, single-line JSON is emitted, and parses cleanly.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    formatter = JSONFormatter()
    handler.setFormatter(formatter)

    logger = logging.getLogger("test_circular_self")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    a: dict[str, Any] = {}
    a["self"] = a

    # Must not raise ValueError: Circular reference detected
    logger.info("Testing circular dict self-reference", extra={"self": a})

    output = stream.getvalue().strip()
    assert "\n" not in output, "Log output must be strictly a single line"
    assert "\r" not in output, "Log output must not contain carriage return"

    parsed = json.loads(output)
    assert parsed["message"] == "Testing circular dict self-reference"
    assert "self" in parsed
    assert "{'self': {...}}" in str(parsed["self"]) or "{...}" in str(parsed["self"])


@pytest.mark.unit
def test_circular_reference_mutual_dicts() -> None:
    """Mutual circular references between two dicts."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_circular_mutual")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    d1: dict[str, Any] = {"name": "d1"}
    d2: dict[str, Any] = {"name": "d2"}
    d1["ref"] = d2
    d2["ref"] = d1

    logger.info("Mutual circular dicts", extra={"d1": d1, "d2": d2})

    output = stream.getvalue().strip()
    assert "\n" not in output
    parsed = json.loads(output)
    assert parsed["message"] == "Mutual circular dicts"
    assert "d1" in parsed and "d2" in parsed


@pytest.mark.unit
def test_circular_reference_list() -> None:
    """Circular list containing itself."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_circular_list")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    l: list[Any] = [1, 2]
    l.append(l)

    logger.info("Circular list", extra={"cycle_list": l})

    output = stream.getvalue().strip()
    assert "\n" not in output
    parsed = json.loads(output)
    assert parsed["message"] == "Circular list"
    assert "cycle_list" in parsed
    assert "[...]" in str(parsed["cycle_list"])


@pytest.mark.unit
def test_circular_reference_deeply_nested() -> None:
    """Deeply nested circular reference inside multiple containers."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_circular_deep")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    a: dict[str, Any] = {}
    a["self"] = a
    nested = {"level1": {"level2": {"level3": {"deep_cycle": a}}}}

    logger.info("Deeply nested cycle", extra={"tree": nested})

    output = stream.getvalue().strip()
    assert "\n" not in output
    parsed = json.loads(output)
    assert parsed["message"] == "Deeply nested cycle"
    assert "tree" in parsed


@pytest.mark.unit
def test_circular_reference_in_msg_itself() -> None:
    """Circular dictionary passed as the log message argument itself."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_circular_msg")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    a: dict[str, Any] = {}
    a["self"] = a

    logger.info(a)

    output = stream.getvalue().strip()
    assert "\n" not in output
    parsed = json.loads(output)
    assert "{'self': {...}}" in parsed["message"] or "{...}" in parsed["message"]


@pytest.mark.unit
def test_circular_reference_recursive_str_repr_recursion_safety() -> None:
    """Object with recursive __str__ and __repr__ loops does not trigger unhandled RecursionError."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_circular_recursion")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    class InfiniteLoopObject:
        def __init__(self):
            self.ref = self
        def __str__(self):
            return f"Loop({str(self.ref)})"
        def __repr__(self):
            return f"Loop({repr(self.ref)})"

    logger.info("Infinite loop object", extra={"bad_obj": InfiniteLoopObject()})

    output = stream.getvalue().strip()
    assert "\n" not in output
    parsed = json.loads(output)
    assert parsed["message"] == "Infinite loop object"
    assert "bad_obj" in parsed
    assert "InfiniteLoopObject" in parsed["bad_obj"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Hostile X-Correlation-ID Header Injection Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize(
    "name,cid_input",
    [
        ("crlf_injection", "evil\r\nX-Injected: attack"),
        ("lf_injection", "evil\nX-Injected: attack"),
        ("cr_injection", "evil\rX-Injected: attack"),
        ("null_byte_mid", "evil\x00cid"),
        ("null_byte_start", "\x00evil"),
        ("null_byte_end", "evil\x00"),
        ("500_chars", "A" * 500),
        ("65_chars", "A" * 65),
        ("surrogate_d800", "\ud800"),
        ("surrogate_dfff", "\udfff"),
        ("emoji", "cid-🚀"),
        ("chinese", "cid-中文"),
        ("xss", "<script>alert(1)</script>"),
        ("sqli", "\"; DROP TABLE logs; --"),
        ("path_traversal", "../../etc/passwd"),
        ("shell", "; rm -rf / ;"),
        ("internal_space", "cid with space"),
        ("tab", "cid\twith\ttab"),
        ("symbols", "cid!@#$%^&*()"),
        ("empty_string", ""),
        ("whitespace_only", "   \t\n  "),
    ],
)
def test_hostile_correlation_id_fallback_to_uuid4(name: str, cid_input: str) -> None:
    """
    Empirical Challenge 3: Hostile correlation IDs must fail regex validation
    and fall back to a newly generated UUID4 string.
    """
    # 1. Unit validation function
    validated = _validate_or_generate_correlation_id(cid_input)
    assert validated != cid_input or cid_input == "", f"Hostile CID '{name}' was not rejected!"
    # Must be valid UUID4
    parsed_uuid = uuid.UUID(validated, version=4)
    assert str(parsed_uuid) == validated

    # 2. HTTP response header propagation
    client = TestClient(app)
    try:
        resp = client.get("/api/health", headers={"X-Correlation-ID": cid_input})
        out_cid = resp.headers.get("X-Correlation-ID")
        assert out_cid is not None
        out_uuid = uuid.UUID(out_cid, version=4)
        assert str(out_uuid) == out_cid
    except UnicodeEncodeError:
        # Standard HTTP client rejects surrogates at transport layer
        pass


@pytest.mark.unit
def test_valid_correlation_id_boundary_lengths() -> None:
    """Valid correlation IDs (1 char, 64 chars) with safe characters are preserved."""
    client = TestClient(app)

    # 1 char valid
    r1 = client.get("/api/health", headers={"X-Correlation-ID": "A"})
    assert r1.headers.get("X-Correlation-ID") == "A"

    # 64 chars valid
    cid_64 = "a" * 64
    r64 = client.get("/api/health", headers={"X-Correlation-ID": cid_64})
    assert r64.headers.get("X-Correlation-ID") == cid_64

    # Valid hyphen and underscore
    cid_safe = "req_123-abc_DEF"
    r_safe = client.get("/api/health", headers={"X-Correlation-ID": cid_safe})
    assert r_safe.headers.get("X-Correlation-ID") == cid_safe


# ─────────────────────────────────────────────────────────────────────────────
# 3. Query Parameter Injection & Redaction Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_query_param_sanitization_standard_keys() -> None:
    """Standalone sensitive keys must all be redacted."""
    sensitive_keys = [
        "token", "TOKEN", "Token",
        "secret", "SECRET",
        "password", "Password",
        "key", "KEY",
        "auth", "AUTH",
        "api_key", "API_KEY",
        "apikey", "APIKEY",
        "access_token", "ACCESS_TOKEN",
        "refresh_token", "REFRESH_TOKEN",
        "authorization", "AUTHORIZATION",
        "user_password", "client_secret", "api-key", "my_token",
    ]
    for k in sensitive_keys:
        assert _is_sensitive_param_name(k) is True, f"Key '{k}' expected to be sensitive"


@pytest.mark.unit
def test_query_param_sanitization_url_encoded_keys_and_values() -> None:
    """URL-encoded parameter keys and values are properly sanitized."""
    # URL encoded key: %74%6f%6b%65%6e is "token"
    qp = QueryParams("%74%6f%6b%65%6e=mysecret&safe_param=hello")
    sanitized = _sanitize_query_params(qp)
    assert "token=[REDACTED]" in sanitized
    assert "mysecret" not in sanitized
    assert "safe_param=hello" in sanitized

    # URL encoded value
    qp2 = QueryParams("token=%73%65%63%72%65%74%31%32%33")
    sanitized2 = _sanitize_query_params(qp2)
    assert "token=[REDACTED]" in sanitized2
    assert "secret123" not in sanitized2


@pytest.mark.unit
def test_query_param_sanitization_nested_bracket_vulnerabilities() -> None:
    """
    Empirical Challenge 2 Vulnerability Audit:
    Demonstrates which nested parameter keys are successfully redacted vs which
    leak due to regex delimiter limitations in `_is_sensitive_param_name`.
    """
    # 1. Nested keys containing 'token', 'secret', 'password', 'api_key', 'apikey' ARE redacted
    # because of the substring check: for strong in ("secret", "password", "token", "api_key", "apikey")
    assert _is_sensitive_param_name("nested[token]") is True
    assert _is_sensitive_param_name("nested[secret]") is True
    assert _is_sensitive_param_name("nested[password]") is True
    assert _is_sensitive_param_name("nested[api_key]") is True
    assert _is_sensitive_param_name("nested[apikey]") is True
    assert _is_sensitive_param_name("user[password]") is True
    assert _is_sensitive_param_name("auth[token]") is True

    # 2. VULNERABILITY RESOLUTION:
    # Keys containing 'key', 'auth', or 'authorization' in nested brackets or dot notation
    # are now correctly tokenized via universal non-alphanumeric delimiters.
    # Therefore, they evaluate to sensitive=True and redact sensitive values.
    remediated_keys = [
        "auth[key]",
        "nested[key]",
        "user[key]",
        "credentials[key]",
        "config[auth]",
        "api[key]",
        "user.key",
        "auth.key",
        "headers[authorization]",
        "nested[auth_key]",
    ]
    remediated_findings = {k: _is_sensitive_param_name(k) for k in remediated_keys}

    # Verify that the vulnerability is closed and all evaluate to True
    for k, is_sens in remediated_findings.items():
        assert is_sens is True, (
            f"Expected key '{k}' to be detected as sensitive (returned {is_sens})"
        )

    # Verify that query params with these keys redact raw secrets
    qp_leak = QueryParams("auth[key]=supersecret_key&config[auth]=bearer_token&safe=1")
    leak_output = _sanitize_query_params(qp_leak)
    assert "supersecret_key" not in leak_output, "Vulnerability resolved: auth[key] secret redacted from query_params!"
    assert "bearer_token" not in leak_output, "Vulnerability resolved: config[auth] secret redacted from query_params!"
    assert "auth[key]=[REDACTED]" in leak_output
    assert "config[auth]=[REDACTED]" in leak_output
    assert "safe=1" in leak_output
