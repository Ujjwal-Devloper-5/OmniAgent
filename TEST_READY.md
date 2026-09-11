# TEST_READY.md — OmniAgent Phase 7 Test Suite Ready

**Author:** `teamwork_preview_test_writer_tt` (E2E Testing Track Lead)  
**Date:** 2026-09-11  
**Project:** OmniAgent Phase 7 Enterprise Production Hardening  
**Target:** `tests/` E2E Test Suite & Testing Infrastructure  

---

## 1. Test Suite Status: READY & VERIFIED

The comprehensive 4-Tier test suite for OmniAgent Phase 7 has been designed, authored, and verified in `tests/`. All 5 Acceptance Criteria and core requirements from `ORIGINAL_REQUEST.md` (header `## 2026-09-11T01:29:41Z`) and `PROJECT.md` are covered across 60 automated test cases.

### Test Inventory Summary

| Test File | Acceptance Criteria & Scope | Test Count | Unit / Mock Pass | Deselected (Live) | Pending Implementation |
|---|---|:---:|:---:|:---:|:---:|
| `tests/test_docker_stack.py` | **AC 1 & R1**: Docker compose services (8 containers), port bindings, socket proxy, network isolation | 10 | 2 | 1 | 7 (M4) |
| `tests/test_rate_limiter.py` | **AC 2 & R2**: Redis sliding-window rate limiting, token budgets, atomic Lua scripts, circuit breaker | 11 | 10 | 1 | 0 (M2 integration) |
| `tests/test_storage.py` | **AC 3 & R2**: MinIO S3 upload/download, pre-signed URLs (GET/PUT), dual endpoints, expiry | 13 | 12 | 1 | 0 (M3 integration) |
| `tests/test_observability.py` | **AC 4 & R3**: `/metrics` Prometheus endpoint, metric families, `/health` alias, cardinality | 7 | 3 | 2 | 2 (M1) |
| `tests/test_logging.py` | **R3**: JSONFormatter single-line JSON, ISO-8601 UTC, correlation ID propagation & middleware | 10 | 9 | 0 | 1 (M1) |
| `tests/test_sandbox_socket_proxy.py` | **AC 5 & R1**: Sandbox container lifecycle via proxy, command blocklist, API restriction (403) | 9 | 7 | 2 | 0 (M4 integration) |
| **Total** | **Full Phase 7 Master Plan Verification** | **60** | **43** | **7** | **10** |

---

## 2. 4-Tier Test Design Matrix Verification

```
┌────────────────────────────────────────────────────────────┐
│ Tier 4: Real-World Scenarios & Live Verification           │
│ - Live Docker stack health & port binding verification     │
│ - Live Redis sliding-window burst rate limit enforcement   │
│ - Live MinIO upload, presigned GET/PUT download via httpx  │
│ - Live Prometheus scraping of http://localhost:8080/metrics│
│ - Live sandbox execution via DOCKER_HOST proxy gateway     │
└─────────────────────────────▲──────────────────────────────┘
                              │
┌─────────────────────────────┴──────────────────────────────┐
│ Tier 3: Cross-Feature Interactions                         │
│ - Request Correlation ID propagation -> Structured Logs    │
│ - HTTP requests -> Dynamic Prometheus latency histograms   │
│ - Token consumption -> Dynamic token counter increments    │
│ - Dual-endpoint resolution: data plane internal vs browser │
│ - Multi-tenant rate limit user isolation                   │
└─────────────────────────────▲──────────────────────────────┘
                              │
┌─────────────────────────────┴──────────────────────────────┐
│ Tier 2: Boundary & Security Hardening                      │
│ - Sliding-window boundary edge transitions                 │
│ - Concurrent sub-second microsecond burst load             │
│ - 0-byte and 4MB payload S3 upload integrity               │
│ - Malicious command pattern blocklist enforcement          │
│ - Prohibited Docker API endpoints blocked (HTTP 403)       │
│ - Host /var/run/docker.sock isolation & cap drops          │
└─────────────────────────────▲──────────────────────────────┘
                              │
┌─────────────────────────────┴──────────────────────────────┐
│ Tier 1: Feature Coverage (Opaque-Box Happy Path)           │
│ - All 8 enterprise services defined                        │
│ - Sliding window allows requests under RPM threshold       │
│ - Exceeding RPM returns allowed=False with wait seconds    │
│ - MinIO S3 byte and file upload / download                 │
│ - Presigned GET URL returns valid AWS SigV4 query params   │
│ - Presigned PUT URL supports client-side uploads           │
│ - /metrics and /health return HTTP 200 with required format│
│ - JSONFormatter outputs valid single-line JSON             │
│ - Sandbox tool executes commands inside containers         │
└────────────────────────────────────────────────────────────┘
```

---

## 3. How to Execute the Test Suite

### 3.1 Unit & Mock Mode (Offline / Fast CI Gating)
Runs the 53 isolated unit tests without requiring active Docker containers:
```bash
uv run pytest tests/ -m "not live" -v
```

### 3.2 Individual Module Test Execution
```bash
# Rate Limiting (AC 2)
uv run pytest tests/test_rate_limiter.py -v

# Storage & Pre-signed URLs (AC 3)
uv run pytest tests/test_storage.py -v

# Observability & Metrics (AC 4)
uv run pytest tests/test_observability.py -v

# Structured Logging (R3)
uv run pytest tests/test_logging.py -v

# Docker Stack (AC 1)
uv run pytest tests/test_docker_stack.py -v

# Sandbox & Socket Proxy (AC 5)
uv run pytest tests/test_sandbox_socket_proxy.py -v
```

### 3.3 Live Stack Acceptance Mode (Cluster E2E)
Once `docker compose up -d` brings up the enterprise stack:
```bash
uv run pytest tests/ -m "live" -v
```

### 3.4 Full Test Suite Execution
```bash
uv run pytest tests/ -v
```

---

## 4. Discovered Implementation Gaps (Escalated to Milestones M1 & M4)

The test suite executed against the current repository state verified 43 test cases and precisely identified the 10 outstanding implementation gaps:

1. **Milestone M1 (Observability & Logging)**:
   - `adapters/admin_api.py`: Needs unauthenticated `GET /metrics` route returning Prometheus metrics (failed `test_admin_api_metrics_endpoint_unauthenticated`).
   - `adapters/admin_api.py`: Needs `GET /health` alias for Docker healthcheck (failed `test_admin_api_health_endpoint`).
   - `adapters/admin_api.py`: Needs `CorrelationIdMiddleware` injecting `X-Correlation-ID` header in responses and contextvars (failed `test_admin_api_correlation_id_middleware`).
   - `core/metrics.py`: Needs creation with the 5 metric families (`omniagent_http_request_duration_seconds`, `omniagent_token_usage_total`, `omniagent_model_fallbacks_total`, `omniagent_errors_total`, `omniagent_active_requests`).

2. **Milestone M4 (Docker Security & Compose Hardening)**:
   - `docker-compose.yml`: Needs addition of `redis`, `minio`, `minio-init`, `prometheus`, `grafana`, and `docker-socket-proxy:0.1.2` (failed `test_all_eight_enterprise_services_present`).
   - `docker-compose.yml`: Needs `omniagent` port mapping changed to `8080:8080` and `grafana` mapped to `3001:3000` (failed `test_port_mappings_non_colliding`).
   - `docker-compose.yml`: Needs `/var/run/docker.sock` removed from `omniagent` (failed `test_omniagent_container_socket_isolation`).
   - `docker-compose.yml`: Needs `SYS_ADMIN` and `seccomp=unconfined` dropped from `omniagent` (failed `test_omniagent_drops_sys_admin_and_unconfined`).
   - `docker-compose.yml`: Needs proxy service configured with least-privilege flags and read-only socket mount (failed `test_docker_socket_proxy_least_privilege_flags`).
   - `docker-compose.yml`: Needs `socket_proxy_net` defined with `internal: true` (failed `test_internal_network_isolation_configuration`).

---

## 5. Milestone Gating Instructions

- **Milestone M1**: Must make `tests/test_observability.py` and `tests/test_logging.py` pass 100%.
- **Milestone M2**: Must verify rewritten `core/rate_limiter.py` passes `tests/test_rate_limiter.py` 100%.
- **Milestone M3**: Must verify `core/storage.py` and `tools/upload_tool.py` pass `tests/test_storage.py` 100%.
- **Milestone M4**: Must make `tests/test_docker_stack.py` and `tests/test_sandbox_socket_proxy.py` pass 100%.
- **Milestone M5 (Final Acceptance)**: Must execute `uv run pytest tests/ -v` and achieve 100% pass across all 60 tests (both mock and live).
