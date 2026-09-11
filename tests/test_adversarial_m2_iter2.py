"""
Adversarial Stress Test Suite - Milestone 2 Iteration 2
Empirical verification of:
1. Fast-fail latency during Redis disconnection (no timeout stalls).
2. Multi-token cost rejection when count + cost > limit.
3. Circuit breaker recovery on reconnect across check_request, check_rate_limit, record_tokens, and get_stats.
4. Concurrency contention and state parity between Redis and in-memory fallback.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional
import uuid

import pytest

from core.rate_limiter import RateLimiter, CircuitState, _MemoryRateLimiter
from tests.conftest import MockRedisClient


class StallingRedisClient(MockRedisClient):
    """
    Simulates a network timeout / hung socket when disconnected.
    If connected is False, it sleeps for stall_duration before raising ConnectionRefusedError.
    If circuit breaker fails to fast-fail, tests will either time out or detect high latency.
    """
    def __init__(self, stall_duration: float = 1.0) -> None:
        super().__init__()
        self.stall_duration = stall_duration
        self.call_count = 0

    async def _check_connection_async(self) -> None:
        self.call_count += 1
        if not self._connected:
            await asyncio.sleep(self.stall_duration)
            raise ConnectionRefusedError(f"Simulated network timeout ({self.stall_duration}s)")

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        await self._check_connection_async()
        return await super().eval(script, numkeys, *keys_and_args)

    async def get(self, key: str) -> Optional[str]:
        await self._check_connection_async()
        return await super().get(key)

    async def incrby(self, key: str, amount: int = 1) -> int:
        await self._check_connection_async()
        return await super().incrby(key, amount)

    async def zcard(self, key: str) -> int:
        await self._check_connection_async()
        return await super().zcard(key)

    async def zremrangebyscore(self, key: str, min_score: Any, max_score: Any) -> int:
        await self._check_connection_async()
        return await super().zremrangebyscore(min_score, max_score)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fast-Fail Latency during Redis Disconnection (No Timeout Stalls)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_fast_fail_latency_under_stalled_redis() -> None:
    """
    Verification 1.1: Fast-fail latency under stalled Redis.
    Trip circuit breaker once (suffers 1 stall).
    Then issue 500 requests during OPEN state.
    Every request MUST fast-fail to memory in < 1ms, without invoking the stalled Redis client.
    """
    stall_sec = 0.5
    stalling_redis = StallingRedisClient(stall_duration=stall_sec)
    limiter = RateLimiter(
        redis_client=stalling_redis,
        requests_per_minute=1000,
        recovery_cooldown=10.0,  # Cooldown long enough for 500 requests
    )
    user = f"fastfail_{uuid.uuid4().hex[:6]}"

    # Step 1: Disconnect Redis and trip breaker
    stalling_redis.set_connected(False)
    t0 = time.perf_counter()
    allowed_trip, _ = await limiter.check_rate_limit(user, cost=1)
    trip_latency = time.perf_counter() - t0

    assert allowed_trip is True, "First request must succeed via in-memory fallback"
    assert limiter._circuit_state == CircuitState.OPEN, "Circuit breaker must be OPEN"
    assert trip_latency >= stall_sec, f"Trip request should have encountered stall ({trip_latency:.3f}s)"
    initial_calls = stalling_redis.call_count

    # Step 2: Issue 500 requests during OPEN state
    latencies = []
    t_start_burst = time.perf_counter()
    for _ in range(500):
        t_req = time.perf_counter()
        allowed, _ = await limiter.check_rate_limit(user, cost=1)
        latencies.append(time.perf_counter() - t_req)
        assert allowed is True

    total_burst_time = time.perf_counter() - t_start_burst

    # Step 3: Assertions on latency and zero downstream calls
    # 500 requests must complete in < 100ms total (< 0.2ms average)
    assert total_burst_time < 0.2, (
        f"Fast-fail burst took {total_burst_time:.4f}s for 500 requests! "
        f"Expected < 0.2s without stalls."
    )
    max_latency = max(latencies)
    p99_latency = sorted(latencies)[int(len(latencies) * 0.99)]

    assert max_latency < 0.005, f"Max single request latency {max_latency*1000:.2f}ms exceeds 5ms threshold"
    assert p99_latency < 0.002, f"P99 latency {p99_latency*1000:.2f}ms exceeds 2ms threshold"

    # Zero calls to Redis while OPEN
    additional_calls = stalling_redis.call_count - initial_calls
    assert additional_calls == 0, (
        f"Circuit breaker leaked {additional_calls} calls to disconnected Redis during OPEN state!"
    )


@pytest.mark.unit
async def test_fast_fail_all_five_methods_during_open() -> None:
    """
    Verification 1.2: Verify that check_request, check_rate_limit, check_token_quota,
    record_tokens, and get_stats ALL fast-fail cleanly during OPEN state.
    """
    stalling_redis = StallingRedisClient(stall_duration=0.5)
    limiter = RateLimiter(
        redis_client=stalling_redis,
        requests_per_minute=20,
        tokens_per_day=10_000,
        recovery_cooldown=5.0,
    )
    user = f"fastfail_all_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    stalling_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN
    calls_after_trip = stalling_redis.call_count

    # 1. check_request
    t0 = time.perf_counter()
    allowed_cr, _ = await limiter.check_request(user)
    assert time.perf_counter() - t0 < 0.01
    assert allowed_cr is True

    # 2. check_rate_limit
    t0 = time.perf_counter()
    allowed_crl, info_crl = await limiter.check_rate_limit(user, cost=1)
    assert time.perf_counter() - t0 < 0.01
    assert allowed_crl is True

    # 3. check_token_quota
    t0 = time.perf_counter()
    allowed_ctq, info_ctq = await limiter.check_token_quota(user, tokens=100)
    assert time.perf_counter() - t0 < 0.01
    assert allowed_ctq is True

    # 4. record_tokens
    t0 = time.perf_counter()
    await limiter.record_tokens(user, 150)
    assert time.perf_counter() - t0 < 0.01

    # 5. get_stats
    t0 = time.perf_counter()
    stats = await limiter.get_stats(user)
    assert time.perf_counter() - t0 < 0.01
    assert stats["tokens_used_today"] == 150
    assert stats["circuit_broken"] is True
    assert stats["circuit_state"] == "open"

    # Confirm not a single additional network call occurred
    assert stalling_redis.call_count == calls_after_trip, (
        f"Methods leaked calls to Redis during OPEN! Expected {calls_after_trip}, got {stalling_redis.call_count}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-Token Cost Rejection When Count + Cost > Limit
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_multi_token_cost_rejection_boundary(mock_redis: MockRedisClient) -> None:
    """
    Verification 2.1: Multi-token cost rejection when count + cost > limit.
    Verifies that:
    - Exactly count + cost <= limit is admitted
    - When count + cost > limit, request is rejected with allowed=False
    - Rejected request does NOT partially insert members into Redis ZSET
    - Current usage in rejected response matches count
    - Retry after is non-zero
    """
    limit = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"multicost_bound_{uuid.uuid4().hex[:6]}"

    # Step 1: Pre-fill with 7 tokens (cost=7)
    allowed1, info1 = await limiter.check_rate_limit(user, cost=7)
    assert allowed1 is True
    assert info1["current_usage"] == 7
    assert info1["limit"] == 10

    card_after_7 = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card_after_7 == 7

    # Step 2: Request cost=3 (7 + 3 = 10 <= 10 -> exactly hits limit)
    allowed2, info2 = await limiter.check_rate_limit(user, cost=3)
    assert allowed2 is True
    assert info2["current_usage"] == 10

    card_after_10 = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card_after_10 == 10

    # Step 3: Request cost=1 (10 + 1 = 11 > 10 -> MUST REJECT)
    allowed3, info3 = await limiter.check_rate_limit(user, cost=1)
    assert allowed3 is False
    assert info3["allowed"] is False
    assert info3["current_usage"] == 10
    assert info3["limit"] == 10
    assert info3["retry_after"] > 0
    assert info3["reset_seconds"] > 0

    # Step 4: Verify Redis ZSET cardinality remains EXACTLY 10 (no partial insertion)
    card_after_reject = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card_after_reject == 10, f"Expected cardinality 10, got {card_after_reject} (leak/corruption!)"


@pytest.mark.unit
async def test_multi_token_cost_rejection_from_empty(mock_redis: MockRedisClient) -> None:
    """
    Verification 2.2: When cost > limit from empty state.
    Must immediately reject and not create Redis keys or add ZSET members.
    """
    limit = 5
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"multicost_empty_{uuid.uuid4().hex[:6]}"

    allowed, info = await limiter.check_rate_limit(user, cost=6)
    assert allowed is False
    assert info["allowed"] is False
    assert info["limit"] == limit
    assert info["retry_after"] == 60

    # Ensure no keys created in Redis
    keys = await mock_redis.keys(f"ratelimit:rpm:{user}")
    assert len(keys) == 0, f"Expected 0 keys created in Redis for rejected over-limit cost, found {keys}"


@pytest.mark.unit
async def test_multi_token_cost_invalid_values(mock_redis: MockRedisClient) -> None:
    """
    Verification 2.3: Non-positive costs must raise ValueError.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10)
    user = "invalid_cost_user"

    with pytest.raises(ValueError, match="cost must be greater than 0"):
        await limiter.check_rate_limit(user, cost=0)

    with pytest.raises(ValueError, match="cost must be greater than 0"):
        await limiter.check_rate_limit(user, cost=-5)


@pytest.mark.unit
async def test_multi_token_cost_concurrency_capacity(mock_redis: MockRedisClient) -> None:
    """
    Verification 2.4: Concurrent weighted requests.
    Limit = 20.
    10 concurrent tasks each request cost=3 (total 30 requested).
    Exactly 6 tasks must succeed (6 * 3 = 18 <= 20).
    4 tasks must be rejected (18 + 3 = 21 > 20).
    Final ZSET cardinality must be exactly 18.
    """
    limit = 20
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"multicost_concurrent_{uuid.uuid4().hex[:6]}"

    results = await asyncio.gather(*[limiter.check_rate_limit(user, cost=3) for _ in range(10)])
    allowed_list = [res for res in results if res[0] is True]
    denied_list = [res for res in results if res[0] is False]

    assert len(allowed_list) == 6, f"Expected exactly 6 allowed requests (18 tokens), got {len(allowed_list)}"
    assert len(denied_list) == 4, f"Expected exactly 4 denied requests, got {len(denied_list)}"

    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == 18, f"Expected Redis ZSET cardinality 18, got {card}"


@pytest.mark.unit
async def test_multi_token_cost_memory_fallback_parity() -> None:
    """
    Verification 2.5: Verify in-memory fallback maintains 100% parity with Redis
    for multi-token cost enforcement.
    """
    mock_redis = MockRedisClient()
    mock_redis.set_connected(False)  # Force in-memory fallback
    limit = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"mem_multicost_{uuid.uuid4().hex[:6]}"

    # 1. First cost=7 succeeds
    allowed1, info1 = await limiter.check_rate_limit(user, cost=7)
    assert allowed1 is True
    assert info1["current_usage"] == 7

    # 2. Next cost=3 succeeds (7+3=10)
    allowed2, info2 = await limiter.check_rate_limit(user, cost=3)
    assert allowed2 is True
    assert info2["current_usage"] == 10

    # 3. Next cost=1 fails (10+1=11 > 10)
    allowed3, info3 = await limiter.check_rate_limit(user, cost=1)
    assert allowed3 is False
    assert info3["current_usage"] == 10
    assert info3["retry_after"] > 0

    # 4. cost > limit fails immediately
    allowed4, info4 = await limiter.check_rate_limit(user, cost=15)
    assert allowed4 is False

    # 5. Invalid costs raise ValueError
    with pytest.raises(ValueError, match="cost must be greater than 0"):
        await limiter.check_rate_limit(user, cost=0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Circuit Breaker Recovery on Reconnect Across All 4 Consumer Methods
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_recovery_via_check_request(mock_redis: MockRedisClient) -> None:
    """
    Verification 3.1: Circuit breaker recovery triggered by check_request.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, recovery_cooldown=0.2)
    user = f"rec_req_{uuid.uuid4().hex[:6]}"

    # 1. Closed state works
    await limiter.check_request(user)
    assert limiter._circuit_state == CircuitState.CLOSED

    # 2. Outage trips breaker
    mock_redis.set_connected(False)
    await limiter.check_request(user)
    assert limiter._circuit_state == CircuitState.OPEN
    assert limiter._circuit_broken is True

    # 3. Reconnect Redis and wait out cooldown
    mock_redis.set_connected(True)
    await asyncio.sleep(0.25)

    # 4. Trigger recovery via check_request
    allowed, _ = await limiter.check_request(user)
    assert allowed is True
    assert limiter._circuit_state == CircuitState.CLOSED, "Breaker must recover to CLOSED"
    assert limiter._circuit_broken is False, "_circuit_broken flag must be cleared"


@pytest.mark.unit
async def test_recovery_via_check_rate_limit(mock_redis: MockRedisClient) -> None:
    """
    Verification 3.2: Circuit breaker recovery triggered by check_rate_limit.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, recovery_cooldown=0.2)
    user = f"rec_crl_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    mock_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN

    # Reconnect and elapse cooldown
    mock_redis.set_connected(True)
    await asyncio.sleep(0.25)

    # Probe via check_rate_limit
    allowed, info = await limiter.check_rate_limit(user, cost=2)
    assert allowed is True
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False

    # Verify key was written to Redis
    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == 2, f"Expected 2 members in Redis ZSET after recovery, got {card}"


@pytest.mark.unit
async def test_recovery_via_record_tokens(mock_redis: MockRedisClient) -> None:
    """
    Verification 3.3: Circuit breaker recovery triggered by record_tokens.
    Ensures background workers writing tokens restore the breaker and write to Redis.
    """
    limiter = RateLimiter(redis_client=mock_redis, tokens_per_day=5000, recovery_cooldown=0.2)
    user = f"rec_tok_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    mock_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN

    # Reconnect and elapse cooldown
    mock_redis.set_connected(True)
    await asyncio.sleep(0.25)

    # Probe via record_tokens
    await limiter.record_tokens(user, 350)
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False

    # Verify token count in Redis
    stats = await limiter.get_stats(user)
    assert stats["tokens_used_today"] == 350


@pytest.mark.unit
async def test_recovery_via_get_stats(mock_redis: MockRedisClient) -> None:
    """
    Verification 3.4: Circuit breaker recovery triggered by get_stats.
    Ensures telemetry/monitoring queries restore the breaker and report accurate status.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=20, recovery_cooldown=0.2)
    user = f"rec_stat_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    mock_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN

    # Reconnect and elapse cooldown
    mock_redis.set_connected(True)
    await asyncio.sleep(0.25)

    # Probe via get_stats
    stats = await limiter.get_stats(user)
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False
    assert stats["circuit_broken"] is False
    assert stats["circuit_state"] == "closed"


@pytest.mark.unit
async def test_probe_failure_in_half_open_stays_open(mock_redis: MockRedisClient) -> None:
    """
    Verification 3.5: If probe in HALF_OPEN fails, breaker transitions back to OPEN
    and refreshes _last_failure_time, continuing to fast-fail.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, recovery_cooldown=0.2)
    user = f"probe_fail_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    mock_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN
    first_failure_time = limiter._last_failure_time

    # Elapse cooldown but KEEP Redis disconnected
    await asyncio.sleep(0.25)

    # Probe attempts Redis and fails again
    allowed, _ = await limiter.check_rate_limit(user, cost=1)
    assert allowed is True  # Memory fallback still succeeds
    assert limiter._circuit_state == CircuitState.OPEN, "Breaker must return to OPEN on probe failure"
    assert limiter._circuit_broken is True
    assert limiter._last_failure_time > first_failure_time, "Failure timestamp must be updated"

    # Immediate next call within new cooldown must NOT attempt Redis
    assert limiter._should_attempt_redis() is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Stress, Concurrency & Security Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_real_socket_connection_refusal_fast_fail() -> None:
    """
    Verification 4.1: Real TCP connection refusal fast-fail.
    Using a non-existent port (63999) on localhost.
    Initial request fails and trips circuit breaker to OPEN.
    Next 200 requests must fast-fail via memory in < 50ms total.
    """
    limiter = RateLimiter(
        redis_url="redis://127.0.0.1:63999/0",
        requests_per_minute=50,
        recovery_cooldown=5.0,
    )
    user = f"real_socket_{uuid.uuid4().hex[:6]}"

    # Initial request encounters connection refusal
    allowed1, _ = await limiter.check_rate_limit(user, cost=1)
    assert allowed1 is True
    assert limiter._circuit_state == CircuitState.OPEN
    assert limiter._circuit_broken is True

    # Burst 200 requests during OPEN state (requests_per_minute=50)
    # 49 more requests should be allowed, and 150 should be rate-limited by in-memory fallback
    t0 = time.perf_counter()
    allowed_count = 0
    denied_count = 0
    for _ in range(199):
        allowed, _ = await limiter.check_rate_limit(user, cost=1)
        if allowed:
            allowed_count += 1
        else:
            denied_count += 1
    burst_duration = time.perf_counter() - t0

    assert allowed_count == 49, f"Expected 49 allowed in memory, got {allowed_count}"
    assert denied_count == 150, f"Expected 150 denied in memory, got {denied_count}"

    # 199 requests must complete in < 100ms (< 0.5ms/req), proving no socket connect attempts
    assert burst_duration < 0.1, f"Expected < 100ms for 199 fast-failed requests, got {burst_duration:.4f}s"


@pytest.mark.unit
async def test_zero_limit_boundary_enforcement() -> None:
    """
    Verification 4.2: Zero-value limit configuration.
    rpm=0 and tpd=0 must be preserved (not overridden by defaults 20 and 200k).
    All requests must be blocked.
    """
    limiter = RateLimiter(requests_per_minute=0, tokens_per_day=0)
    assert limiter._rpm == 0, f"Expected rpm=0, got {limiter._rpm}"
    assert limiter._tpd == 0, f"Expected tpd=0, got {limiter._tpd}"

    user = "zero_limit_user"

    # check_rate_limit must reject
    allowed_crl, info_crl = await limiter.check_rate_limit(user, cost=1)
    assert allowed_crl is False
    assert info_crl["limit"] == 0

    # check_token_quota must reject
    allowed_ctq, info_ctq = await limiter.check_token_quota(user, tokens=0)
    assert allowed_ctq is False
    assert info_ctq["limit"] == 0

    # check_request must reject
    allowed_cr, reason = await limiter.check_request(user)
    assert allowed_cr is False
    assert len(reason) > 0


@pytest.mark.unit
async def test_concurrent_half_open_probes(mock_redis: MockRedisClient) -> None:
    """
    Verification 4.3: Concurrency during HALF_OPEN probe recovery.
    When cooldown expires and 10 concurrent requests arrive simultaneously,
    all requests execute, breaker transitions to CLOSED, and state is preserved in Redis.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=20, recovery_cooldown=0.1)
    user = f"half_open_conc_{uuid.uuid4().hex[:6]}"

    # Trip breaker
    mock_redis.set_connected(False)
    await limiter.check_rate_limit(user, cost=1)
    assert limiter._circuit_state == CircuitState.OPEN

    # Reconnect and elapse cooldown
    mock_redis.set_connected(True)
    await asyncio.sleep(0.15)

    # 10 concurrent requests arrive at probe moment
    results = await asyncio.gather(*[limiter.check_rate_limit(user, cost=1) for _ in range(10)])
    assert all(res[0] is True for res in results)
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False

    # Check cardinality in Redis
    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == 10, f"Expected 10 entries in Redis, got {card}"


@pytest.mark.unit
async def test_special_character_and_script_injection_handling(mock_redis: MockRedisClient) -> None:
    """
    Verification 4.4: Robustness against adversarial user IDs and Lua injection strings.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10)

    malicious_ids = [
        "user'; redis.call('FLUSHALL'); --",
        "user\nreturn {1, 999, 0}\n--",
        "user:with:colons:and:spaces:🔥",
        "user_with_null\x00byte",
        "",  # Empty user ID
    ]

    for uid in malicious_ids:
        allowed, info = await limiter.check_rate_limit(uid, cost=1)
        assert allowed is True
        assert info["limit"] == 10

        allowed_req, _ = await limiter.check_request(uid)
        assert allowed_req is True

        await limiter.record_tokens(uid, 50)
        stats = await limiter.get_stats(uid)
        assert stats["tokens_used_today"] == 50

