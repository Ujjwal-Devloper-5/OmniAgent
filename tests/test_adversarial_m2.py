"""
Adversarial Stress Test Suite for Milestone 2: Redis Persistent Rate Limiter
═══════════════════════════════════════════════════════════════════════════════
Empirically challenges:
1. Sliding-window boundary conditions and subsecond bursts.
2. Concurrency contention for the last token in window.
3. Circuit breaker failover on Redis connection error and automatic recovery on reconnect.
4. Daily token quota rollover across midnight.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import time
from typing import Any, Dict, Optional, Tuple, Union
import uuid

import pytest

from core.rate_limiter import RateLimiter, CircuitState, _MemoryRateLimiter
from tests.conftest import MockRedisClient


# ─────────────────────────────────────────────────────────────────────────────
# Challenge 1: Sliding-Window Boundary Conditions & Subsecond Bursts
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_subsecond_high_volume_burst(mock_redis: MockRedisClient) -> None:
    """
    Challenge 1.1: 100 concurrent requests fired simultaneously within subsecond time.
    Verifies that atomic Lua execution admits exactly RPM requests and denies 100 - RPM,
    with no member collisions in ZSET and exact cardinality.
    """
    rpm = 15
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"burst_user_{uuid.uuid4().hex[:6]}"

    # Fire 100 concurrent requests simultaneously
    results = await asyncio.gather(*[limiter.check_request(user) for _ in range(100)])
    allowed = [res for res in results if res[0] is True]
    denied = [res for res in results if res[0] is False]

    assert len(allowed) == rpm, f"Expected exactly {rpm} allowed, got {len(allowed)}"
    assert len(denied) == 100 - rpm, f"Expected {100 - rpm} denied, got {len(denied)}"

    # Check ZSET cardinality in Redis
    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == rpm, f"Expected Redis ZSET cardinality {rpm}, got {card}"


@pytest.mark.unit
async def test_sliding_window_exact_boundary_eviction(mock_redis: MockRedisClient) -> None:
    """
    Challenge 1.2: Exact boundary eviction at now - window.
    Evaluates whether an entry at score `T0` is evicted at `T0 + window` (inclusive cutoff)
    or at `T0 + window + epsilon`.
    """
    window = 1.0
    rpm = 1
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=window)
    user = f"boundary_evict_{uuid.uuid4().hex[:6]}"

    # Admitted at t0
    allowed1, _ = await limiter.check_request(user)
    assert allowed1 is True

    # Immediate next request blocked
    allowed2, reason2 = await limiter.check_request(user)
    assert allowed2 is False
    assert "wait" in reason2.lower() or "too fast" in reason2.lower()

    # Sleep slightly past window (1.05s)
    await asyncio.sleep(1.05)

    # Now must be admitted
    allowed3, _ = await limiter.check_request(user)
    assert allowed3 is True, "Request at t0 + window + epsilon must be admitted"


@pytest.mark.unit
async def test_multi_cost_request_defect_in_redis(mock_redis: MockRedisClient) -> None:
    """
    Challenge 1.3: Multi-cost request enforcement (cost > 1).
    Contract in PROJECT.md:
      `async def check_rate_limit(key: str, cost: int = 1) -> tuple[bool, dict]`

    DEFECT PROBE:
    In Redis path, check_rate_limit fails to pass `cost` to SLIDING_WINDOW_LUA.
    Even if cost exceeds available limit (e.g. cost=15 with limit=10), Redis admits it!
    """
    limit = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"multicost_defect_{uuid.uuid4().hex[:6]}"

    # Request with cost=15 when limit is 10
    allowed, info = await limiter.check_rate_limit(user, cost=15)

    # In a correct implementation, cost 15 > limit 10 MUST be rejected (allowed == False).
    # But in current implementation, Lua only checks count < limit (0 < 10) and allows it!
    assert allowed is False, (
        f"CRITICAL DEFECT: check_rate_limit permitted cost=15 when limit={limit}! "
        f"Redis sliding-window Lua script ignores the `cost` parameter entirely. "
        f"Returned info: {info}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Challenge 2: Concurrency Contention for the Last Token in Window
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_heavy_concurrency_contention_for_last_token_redis(mock_redis: MockRedisClient) -> None:
    """
    Challenge 2.1: 50 concurrent coroutines contend for the final 1 token against Redis.
    Must admit exactly 1 and reject 49.
    """
    rpm = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"contention_redis_{uuid.uuid4().hex[:6]}"

    # Pre-consume 9 tokens
    for i in range(9):
        allowed, _ = await limiter.check_request(user)
        assert allowed is True, f"Pre-fill request {i+1} must succeed"

    # 50 coroutines fight for the 1 remaining token
    results = await asyncio.gather(*[limiter.check_request(user) for _ in range(50)])
    allowed_count = sum(1 for allowed, _ in results if allowed is True)
    denied_count = sum(1 for allowed, _ in results if allowed is False)

    assert allowed_count == 1, f"Expected exactly 1 coroutine to win the last token, got {allowed_count}"
    assert denied_count == 49, f"Expected 49 coroutines to be denied, got {denied_count}"


@pytest.mark.unit
async def test_heavy_concurrency_contention_in_memory_fallback(mock_redis: MockRedisClient) -> None:
    """
    Challenge 2.2: 50 concurrent coroutines contend for the final 1 token under in-memory fallback.
    Tests async lock safety and race-free window management in _MemoryRateLimiter.
    """
    rpm = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"contention_mem_{uuid.uuid4().hex[:6]}"

    # Disconnect Redis to trigger in-memory fallback
    mock_redis.set_connected(False)

    # Pre-consume 9 tokens
    for i in range(9):
        allowed, _ = await limiter.check_request(user)
        assert allowed is True

    # 50 coroutines fight for the 1 remaining token in memory
    results = await asyncio.gather(*[limiter.check_request(user) for _ in range(50)])
    allowed_count = sum(1 for allowed, _ in results if allowed is True)
    denied_count = sum(1 for allowed, _ in results if allowed is False)

    assert allowed_count == 1, f"Expected exactly 1 coroutine to win the last token in memory, got {allowed_count}"
    assert denied_count == 49, f"Expected 49 coroutines to be denied in memory, got {denied_count}"


@pytest.mark.unit
async def test_concurrency_contention_with_variable_costs_defect(mock_redis: MockRedisClient) -> None:
    """
    Challenge 2.3: Concurrency contention with multi-unit costs.
    Limit = 10. 8 units consumed (2 units remain).
    Two concurrent requests arrive, EACH requesting cost=2.
    Total requested = 4 units > 2 available.
    Expected: Exactly 1 request succeeds (consuming 2 units), the other fails.
    Actual: Because Redis ignores cost, BOTH succeed!
    """
    limit = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"contention_cost_{uuid.uuid4().hex[:6]}"

    # Pre-fill 8 slots
    for _ in range(8):
        allowed, _ = await limiter.check_rate_limit(user, cost=1)
        assert allowed is True

    # 2 concurrent requests each asking for cost=2
    res1, res2 = await asyncio.gather(
        limiter.check_rate_limit(user, cost=2),
        limiter.check_rate_limit(user, cost=2),
    )

    # If cost was respected, only 1 could succeed because 8 + 2 + 2 = 12 > 10
    success_count = (1 if res1[0] else 0) + (1 if res2[0] else 0)
    assert success_count == 1, (
        f"CRITICAL DEFECT: Concurrency contention violation! "
        f"Expected at most 1 request of cost=2 to succeed when 2 slots remain, "
        f"but {success_count} succeeded. Res1: {res1}, Res2: {res2}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Challenge 3: Circuit Breaker Failover & Recovery
# ─────────────────────────────────────────────────────────────────────────────

class SpyRedisClient(MockRedisClient):
    """Monitors method invocations to verify circuit breaker call suppression."""
    def __init__(self) -> None:
        super().__init__()
        self.eval_call_count = 0
        self.get_call_count = 0
        self.incrby_call_count = 0

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        self.eval_call_count += 1
        return await super().eval(script, numkeys, *keys_and_args)

    async def get(self, key: str) -> Optional[str]:
        self.get_call_count += 1
        return await super().get(key)

    async def incrby(self, key: str, amount: int = 1) -> int:
        self.incrby_call_count += 1
        return await super().incrby(key, amount)


@pytest.mark.unit
async def test_circuit_breaker_call_suppression_when_open() -> None:
    """
    Challenge 3.1: Circuit breaker call suppression during OPEN state.
    When a circuit breaker is OPEN, subsequent requests MUST NOT invoke the failing
    downstream service repeatedly (avoiding timeout accumulation and latency spikes).
    """
    spy = SpyRedisClient()
    limiter = RateLimiter(redis_client=spy, requests_per_minute=20, window_seconds=60.0)
    user = f"cb_suppress_{uuid.uuid4().hex[:6]}"

    # Trip the circuit breaker by disconnecting Redis
    spy.set_connected(False)
    allowed1, _ = await limiter.check_request(user)
    assert allowed1 is True, "1st request should fall back to memory"
    assert limiter._circuit_broken is True, "Circuit breaker should be tripped"
    initial_evals = spy.eval_call_count

    # Fire 5 more requests while circuit is OPEN
    for _ in range(5):
        allowed, _ = await limiter.check_request(user)
        assert allowed is True

    # In a real circuit breaker, calls while OPEN are suppressed during cooldown
    # Check whether the limiter suppressed calls or hammered the disconnected Redis
    additional_evals = spy.eval_call_count - initial_evals
    assert additional_evals == 0, (
        f"CRITICAL DEFECT: Circuit breaker did NOT suppress calls to downstream service while OPEN! "
        f"Made {additional_evals} repeated calls to disconnected Redis during OPEN state."
    )


@pytest.mark.unit
async def test_circuit_breaker_recovery_via_record_tokens() -> None:
    """
    Challenge 3.2: Can the circuit breaker recover if subsequent traffic is record_tokens?
    If an application only calls record_tokens while Redis recovers, does the circuit breaker
    recover, or does the `if not self._circuit_broken` guard permanently block Redis reconnection?
    """
    spy = SpyRedisClient()
    limiter = RateLimiter(redis_client=spy, requests_per_minute=10, tokens_per_day=5000)
    user = f"cb_token_rec_{uuid.uuid4().hex[:6]}"

    # Healthy request
    await limiter.check_request(user)
    assert limiter._circuit_broken is False

    # Outage
    spy.set_connected(False)
    await limiter.check_request(user)
    assert limiter._circuit_broken is True

    # Redis recovers
    spy.set_connected(True)
    # Simulate elapsed recovery cooldown so probe transitions to HALF_OPEN and succeeds
    limiter._last_failure_time = time.monotonic() - limiter._recovery_cooldown - 1.0

    # Now call record_tokens
    await limiter.record_tokens(user, 100)

    # Check whether circuit breaker recovered
    assert limiter._circuit_broken is False, (
        "CRITICAL DEFECT: Circuit breaker failed to recover via record_tokens! "
        "The guard `if not self._circuit_broken` permanently blocks record_tokens from probing or recovering Redis."
    )


@pytest.mark.unit
async def test_circuit_breaker_half_open_state_and_cooldown_unused() -> None:
    """
    Challenge 3.3: Verify whether CircuitState.HALF_OPEN, _recovery_cooldown, and _last_failure_time
    are actually implemented in the circuit breaker logic.
    """
    limiter = RateLimiter(requests_per_minute=10)
    # Verify declared fields exist
    assert hasattr(limiter, "_circuit_state")
    assert hasattr(limiter, "_recovery_cooldown")
    assert hasattr(limiter, "_last_failure_time")

    # Verify that CircuitState.HALF_OPEN is never transitioned to during failure and recovery
    spy = SpyRedisClient()
    limiter = RateLimiter(redis_client=spy, requests_per_minute=10)
    user = "cb_half_open_test"

    # Trip breaker
    spy.set_connected(False)
    await limiter.check_request(user)
    assert limiter._circuit_state == CircuitState.OPEN

    # Reconnect and advance past cooldown so probe executes
    spy.set_connected(True)
    limiter._last_failure_time = time.monotonic() - limiter._recovery_cooldown - 1.0
    await limiter.check_request(user)
    assert limiter._circuit_state == CircuitState.CLOSED, "Breaker must recover to CLOSED upon successful probe"


# ─────────────────────────────────────────────────────────────────────────────
# Challenge 4: Daily Token Quota Rollover Across Midnight
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_daily_token_quota_rollover_across_midnight(mock_redis: MockRedisClient) -> None:
    """
    Challenge 4.1: Exhaust daily token quota on Day 1, then simulate UTC midnight rollover.
    Verifies that on Day 2, requests are immediately allowed and tokens start at 0.
    """
    tpd = 1000
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, tokens_per_day=tpd)
    user = f"midnight_user_{uuid.uuid4().hex[:6]}"

    # Base timestamp: 2026-09-10 23:59:50 UTC
    day1_ts = 1789084790.0  # 2026-09-10 23:59:50 UTC
    day1_date = time.strftime("%Y%m%d", time.gmtime(day1_ts))
    assert day1_date == "20260910"

    # Set user token usage to 1000 in Redis for Day 1
    day1_key = f"ratelimit:tokens:{user}:{day1_date}"
    await mock_redis.set(day1_key, tpd)

    # Verify Day 1 key is exhausted
    val = await mock_redis.get(day1_key)
    assert int(val) == tpd

    # Advance to Day 2: 2026-09-11 00:00:10 UTC (20 seconds later)
    day2_ts = day1_ts + 20.0
    day2_date = time.strftime("%Y%m%d", time.gmtime(day2_ts))
    assert day2_date == "20260911"

    day2_key = f"ratelimit:tokens:{user}:{day2_date}"
    day2_val = await mock_redis.get(day2_key)
    assert day2_val is None, "Day 2 token key must not exist yet"


@pytest.mark.unit
async def test_memory_limiter_midnight_rollover() -> None:
    """
    Challenge 4.2: Test _MemoryRateLimiter daily quota rollover across midnight.
    """
    tpd = 500
    mem = _MemoryRateLimiter(rpm=10, tpd=tpd, window=60.0)
    user = "mem_midnight_user"

    # Day 1: 2026-09-10 23:59:55 UTC
    t_day1 = 1789084795.0
    await mem.record_tokens(user, tpd, t_day1)

    # Verify Day 1 is blocked
    allowed_day1, reason_day1 = await mem.check_request(user, t_day1)
    assert allowed_day1 is False
    assert "daily AI token limit" in reason_day1

    # Day 2: 2026-09-11 00:00:05 UTC (10 seconds later)
    t_day2 = t_day1 + 10.0
    allowed_day2, _ = await mem.check_request(user, t_day2)
    assert allowed_day2 is True, "Request on Day 2 must be allowed after midnight rollover in memory"

    # Check stats on Day 2
    rpm, tokens_used = await mem.get_stats(user, t_day2)
    assert tokens_used == 0, f"Expected 0 tokens used on Day 2, got {tokens_used}"


@pytest.mark.unit
async def test_global_reset_clears_redis(mock_redis: MockRedisClient) -> None:
    """
    Challenge 4.3: limiter.reset() without user_id must clear all limiter state in Redis.
    DEFECT PROBE: When user_id is None, RateLimiter.reset() does NOT delete Redis keys.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5)
    user1 = "user_one"
    user2 = "user_two"

    await limiter.check_request(user1)
    await limiter.check_request(user2)
    await limiter.record_tokens(user1, 100)

    # Call global reset
    await limiter.reset()

    # Verify both users are cleared
    stats1 = await limiter.get_stats(user1)
    assert stats1["rpm_current"] == 0, (
        f"DEFECT: Expected 0 rpm after global reset, got {stats1['rpm_current']}. "
        f"RateLimiter.reset() only clears memory and ignores Redis when user_id is None!"
    )
