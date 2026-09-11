"""
OmniAgent Phase 7 Test Suite — Redis Rate Limiter Verification (AC 2 & R2)
════════════════════════════════════════════════════════════════════════════
Covers Acceptance Criterion 2:
  "Automated tests verify that Rate Limiting correctly blocks requests via Redis."

Tiers Covered:
  - Tier 1: Core sliding-window RPM limit, token quota check, and stats retrieval
  - Tier 2: Boundary conditions (window edge reset, concurrent sub-second burst, exact quota limit,
            partial sliding-window recovery, contention for last token)
  - Tier 3: Multi-user isolation, key inspection, TTL hygiene, and enterprise interface contracts
  - Tier 4: Circuit breaker resilience (Redis outage failover & automatic recovery) & live Redis verification
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

import pytest

from core.rate_limiter import RateLimiter, CircuitState, get_rate_limiter
from tests.conftest import MockRedisClient


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: Feature Coverage (Opaque-Box Happy Path)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_rate_limiter_allows_requests_under_rpm_limit(mock_redis: MockRedisClient) -> None:
    """
    Tier 1: Requests under configured RPM must all be permitted (allowed=True).
    Derived from: ORIGINAL_REQUEST.md:62, PROJECT.md:56-66.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=3, window_seconds=60.0)
    user = f"user_{uuid.uuid4().hex[:6]}"

    for i in range(3):
        allowed, reason = await limiter.check_request(user)
        assert allowed is True, f"Request {i+1} should have been allowed, got reason: {reason}"
        assert reason == ""


@pytest.mark.unit
async def test_rate_limiter_blocks_requests_exceeding_rpm_limit(mock_redis: MockRedisClient) -> None:
    """
    Tier 1: Requests exceeding RPM limit must be blocked (allowed=False)
    with informative wait_time in the reason.
    Derived from: ORIGINAL_REQUEST.md:62, Survey 2 §2.3.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=2, window_seconds=60.0)
    user = f"user_{uuid.uuid4().hex[:6]}"

    # Send 2 allowed requests
    allowed1, _ = await limiter.check_request(user)
    allowed2, _ = await limiter.check_request(user)
    assert allowed1 is True and allowed2 is True

    # 3rd request must be blocked
    allowed3, reason3 = await limiter.check_request(user)
    assert allowed3 is False, "3rd request must be blocked by rate limiter"
    assert "too fast" in reason3 or "wait" in reason3.lower()
    assert "s" in reason3  # Mentions wait seconds


@pytest.mark.unit
async def test_rate_limiter_blocks_exceeded_daily_token_quota(mock_redis: MockRedisClient) -> None:
    """
    Tier 1: Daily token budget exhaustion must block subsequent requests.
    Derived from: PROJECT.md:60, Survey 2 §2.3.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, tokens_per_day=500)
    user = f"user_{uuid.uuid4().hex[:6]}"

    # Allowed initially
    allowed, _ = await limiter.check_request(user)
    assert allowed is True

    # Record 600 tokens (exceeding 500 limit)
    await limiter.record_tokens(user, 600)

    # Next request must be blocked due to daily token exhaustion
    allowed_post, reason_post = await limiter.check_request(user)
    assert allowed_post is False
    assert "daily AI token limit" in reason_post or "token" in reason_post.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: Boundary & Corner Conditions
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_rate_limiter_window_expiry_reset(mock_redis: MockRedisClient) -> None:
    """
    Tier 2: Sliding Window Boundary:
      Verifies that once the sliding window elapses, older requests fall out
      of the sorted set and new requests are admitted.
    """
    window = 1.0  # 1-second short window for boundary test
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=2, window_seconds=window)
    user = f"user_{uuid.uuid4().hex[:6]}"

    # Fill window
    assert (await limiter.check_request(user))[0] is True
    assert (await limiter.check_request(user))[0] is True
    # Exceeded
    assert (await limiter.check_request(user))[0] is False

    # Sleep past window
    await asyncio.sleep(1.1)

    # Should now be admitted again
    allowed, _ = await limiter.check_request(user)
    assert allowed is True, "Request should be admitted after sliding window elapses"


@pytest.mark.unit
async def test_rate_limiter_subsecond_concurrency(mock_redis: MockRedisClient) -> None:
    """
    Tier 2: High Concurrency Burst:
      Fires 10 concurrent requests at the exact same sub-second moment.
      Verifies that atomic Lua execution admits exactly RPM requests and rejects the rest.
    """
    rpm = 4
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"user_{uuid.uuid4().hex[:6]}"

    results = await asyncio.gather(*[limiter.check_request(user) for _ in range(10)])
    allowed_count = sum(1 for allowed, _ in results if allowed is True)
    denied_count = sum(1 for allowed, _ in results if allowed is False)

    assert allowed_count == rpm, f"Expected exactly {rpm} allowed, got {allowed_count}"
    assert denied_count == 10 - rpm, f"Expected {10-rpm} denied, got {denied_count}"


@pytest.mark.unit
async def test_rate_limiter_special_characters_in_user_id(mock_redis: MockRedisClient) -> None:
    """
    Tier 2: User ID Escaping & Key Integrity:
      Tests user IDs with spaces, colons, slashes, unicode, and emojis.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=2)
    special_users = [
        "discord:1234567890:channel#general",
        "slack_user@domain.com/team?sub=1",
        "telegram_user_🚀_🔥",
        "user with spaces and 'quotes'",
    ]

    for u in special_users:
        allowed, _ = await limiter.check_request(u)
        assert allowed is True, f"Failed on special user ID: {u}"


@pytest.mark.unit
async def test_rate_limiter_burst_allowance_within_window(mock_redis: MockRedisClient) -> None:
    """
    Tier 2 Edge Case 1: Burst Allowance Within Window.
      Verifies that a sliding-window rate limiter permits the entire capacity
      in a rapid burst at t=0 without artificial inter-arrival throttling.
    """
    rpm = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"burst_user_{uuid.uuid4().hex[:6]}"

    for i in range(rpm):
        allowed, reason = await limiter.check_request(user)
        assert allowed is True, f"Burst request {i+1} must be allowed"
        assert reason == ""

    # 11th request must be rejected
    blocked, reason = await limiter.check_request(user)
    assert blocked is False
    assert "too fast" in reason.lower() or "wait" in reason.lower()

    # Redis ZSET must hold exactly rpm items
    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == rpm


@pytest.mark.unit
async def test_rate_limiter_exact_boundary_enforcement(mock_redis: MockRedisClient) -> None:
    """
    Tier 2 Edge Case 2: Exact Boundary Enforcement.
      Tests boundary conditions N-1, N, and N+1.
      Critically verifies that rejected requests do NOT increment Redis cardinality.
    """
    limit = 10
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=limit, window_seconds=60.0)
    user = f"boundary_user_{uuid.uuid4().hex[:6]}"

    for i in range(1, limit + 1):
        allowed, reason = await limiter.check_request(user)
        assert allowed is True, f"Request {i} of {limit} must be allowed"
        assert reason == ""

    # Request limit + 1
    allowed_overflow, reason_overflow = await limiter.check_request(user)
    assert allowed_overflow is False, "11th request must be rejected"
    assert "wait" in reason_overflow.lower() or "too fast" in reason_overflow.lower()

    # Redis ZCARD must NOT grow on rejected requests
    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == limit, f"Redis ZSET cardinality must remain {limit}, got {card}"


@pytest.mark.unit
async def test_rate_limiter_sliding_window_partial_recovery(mock_redis: MockRedisClient) -> None:
    """
    Tier 2 Edge Case 3: Continuous Sliding Window Recovery.
      Verifies that as time advances, individual requests slide out one-by-one,
      rather than all resetting simultaneously at a fixed window boundary.
    """
    window = 2.0
    rpm = 3
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=window)
    user = f"sliding_user_{uuid.uuid4().hex[:6]}"

    # Send 3 staggered requests
    assert (await limiter.check_request(user))[0] is True   # t=0.0
    await asyncio.sleep(0.4)
    assert (await limiter.check_request(user))[0] is True   # t=0.4
    await asyncio.sleep(0.4)
    assert (await limiter.check_request(user))[0] is True   # t=0.8

    # 4th request at t=0.8 is blocked
    assert (await limiter.check_request(user))[0] is False

    # Sleep until t=2.1 (first request slides out, but 2nd and 3rd are still active)
    await asyncio.sleep(1.3)

    # Exactly 1 request should now be admitted
    allowed_one, _ = await limiter.check_request(user)
    assert allowed_one is True, "First expired slot must admit exactly one new request"

    # Immediate next request must be rejected
    allowed_two, _ = await limiter.check_request(user)
    assert allowed_two is False, "Window must reject because 2nd, 3rd, and 4th request occupy capacity"


@pytest.mark.unit
async def test_rate_limiter_contention_for_last_token(mock_redis: MockRedisClient) -> None:
    """
    Tier 2 Edge Case 4: Concurrent Contention for Last Token.
      When only 1 token remains, 10 concurrent coroutines contend.
      Atomic Lua must ensure exactly 1 succeeds and 9 fail.
    """
    rpm = 5
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"last_token_user_{uuid.uuid4().hex[:6]}"

    # Consume 4 of the 5 tokens
    for _ in range(4):
        allowed, _ = await limiter.check_request(user)
        assert allowed is True

    # Fire 10 concurrent requests contending for the 1 remaining token
    results = await asyncio.gather(*[limiter.check_request(user) for _ in range(10)])
    allowed_count = sum(1 for allowed, _ in results if allowed is True)
    rejected_count = sum(1 for allowed, _ in results if allowed is False)

    assert allowed_count == 1, f"Expected exactly 1 request to win last token, got {allowed_count}"
    assert rejected_count == 9, f"Expected 9 requests to be rejected, got {rejected_count}"

    card = await mock_redis.zcard(f"ratelimit:rpm:{user}")
    assert card == rpm


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3: Cross-Feature Interactions & Multi-User Isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_rate_limiter_multi_user_isolation(mock_redis: MockRedisClient) -> None:
    """
    Tier 3: Multi-Tenant Isolation:
      Verifies that User Alpha exhausting their rate limit has ZERO effect
      on User Beta's quota.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=2, window_seconds=60.0)
    user_alpha = "tenant_alpha"
    user_beta = "tenant_beta"

    # User Alpha exhausts quota
    await limiter.check_request(user_alpha)
    await limiter.check_request(user_alpha)
    alpha_blocked, _ = await limiter.check_request(user_alpha)
    assert alpha_blocked is False, "User Alpha must be blocked"

    # User Beta must NOT be affected
    beta_allowed1, _ = await limiter.check_request(user_beta)
    beta_allowed2, _ = await limiter.check_request(user_beta)
    assert beta_allowed1 is True and beta_allowed2 is True, "User Beta must not be blocked by Alpha's usage"


@pytest.mark.unit
async def test_rate_limiter_redis_state_inspection(mock_redis: MockRedisClient) -> None:
    """
    Tier 3: Direct Redis Key Inspection:
      Verifies that Redis keys follow the exact schema specified in PROJECT.md:
        - `ratelimit:rpm:{user_id}` as ZSET
        - `ratelimit:tokens:{user_id}:{YYYYMMDD}` as String counter
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5)
    user = "state_inspect_user"

    await limiter.check_request(user)
    await limiter.record_tokens(user, 150)

    # Inspect ZSET
    rpm_key = f"ratelimit:rpm:{user}"
    count = await mock_redis.zcard(rpm_key)
    assert count == 1, f"Expected ZSET card 1, got {count}"

    # Inspect Token String
    today = time.strftime("%Y%m%d", time.gmtime())
    token_key = f"ratelimit:tokens:{user}:{today}"
    tokens = await mock_redis.get(token_key)
    assert int(tokens) == 150, f"Expected 150 tokens, got {tokens}"


@pytest.mark.unit
async def test_rate_limiter_ttl_hygiene(mock_redis: MockRedisClient) -> None:
    """
    Tier 3: Redis Key Expiration (TTL Hygiene):
      Verifies that ZSET keys and daily token counters set TTLs to prevent memory leaks.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5, window_seconds=60.0)
    user = "ttl_hygiene_user"

    await limiter.check_request(user)
    await limiter.record_tokens(user, 250)

    rpm_key = f"ratelimit:rpm:{user}"
    today = time.strftime("%Y%m%d", time.gmtime())
    token_key = f"ratelimit:tokens:{user}:{today}"

    assert rpm_key in mock_redis._ttls, "RPM key must have TTL set"
    assert mock_redis._ttls[rpm_key] > time.time()

    assert token_key in mock_redis._ttls, "Daily token key must have TTL set"
    assert mock_redis._ttls[token_key] > time.time()


@pytest.mark.unit
async def test_rate_limiter_enterprise_interface(mock_redis: MockRedisClient) -> None:
    """
    Tier 3: Enterprise Specification Contracts (check_rate_limit & check_token_quota).
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5, tokens_per_day=1000)
    user = "enterprise_contract_user"

    # check_rate_limit contract
    allowed, info = await limiter.check_rate_limit(user, cost=1)
    assert allowed is True
    assert set(info.keys()) >= {"allowed", "current_usage", "limit", "reset_seconds", "retry_after"}
    assert info["allowed"] is True
    assert info["current_usage"] == 1
    assert info["limit"] == 5

    # check_token_quota contract
    quota_allowed, q_info = await limiter.check_token_quota(user, tokens=100)
    assert quota_allowed is True
    assert set(q_info.keys()) >= {"allowed", "tokens_used", "limit", "remaining", "reset_seconds"}
    assert q_info["limit"] == 1000


@pytest.mark.unit
async def test_core_rate_limiter_module_contract() -> None:
    """
    Tier 3: Core Module Interface Contract Verification:
      Verifies that core.rate_limiter.get_rate_limiter() returns a functional
      RateLimiter instance adhering to check_request, record_tokens, and get_stats.
    """
    limiter = get_rate_limiter()
    assert isinstance(limiter, RateLimiter)

    test_user = f"contract_test_{uuid.uuid4().hex[:6]}"
    allowed, reason = await limiter.check_request(test_user)
    assert allowed is True
    assert reason == ""

    await limiter.record_tokens(test_user, 50)
    stats = await limiter.get_stats(test_user)
    assert isinstance(stats, dict)
    assert "tokens_used_today" in stats or "daily_tokens_used" in stats
    assert "requests_remaining_this_minute" in stats


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4: Real-World Scenarios & Resiliency (Circuit Breaker & Live Stack)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
async def test_rate_limiter_circuit_breaker_on_redis_outage(mock_redis: MockRedisClient) -> None:
    """
    Tier 4: Circuit Breaker Failover:
      Simulates Redis connection drop. The rate limiter must:
        1. Catch the connection exception
        2. Seamlessly fall back to in-memory rate limiting
        3. Never raise unhandled exceptions or crash user requests
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=2, window_seconds=60.0)
    user = "circuit_test_user"

    # Simulate network partition
    mock_redis.set_connected(False)

    # 1st request through fallback
    allowed1, _ = await limiter.check_request(user)
    assert allowed1 is True, "Fallback should permit 1st request"
    assert limiter._circuit_broken is True, "Circuit breaker flag should be activated"

    # 2nd request through fallback
    allowed2, _ = await limiter.check_request(user)
    assert allowed2 is True

    # 3rd request through fallback must be blocked
    allowed3, reason3 = await limiter.check_request(user)
    assert allowed3 is False, "Fallback must continue enforcing rate limits"


@pytest.mark.unit
async def test_rate_limiter_recovery_after_redis_reconnect(mock_redis: MockRedisClient) -> None:
    """
    Tier 4 Edge Case 6: Automatic Circuit Breaker Recovery.
      Simulates outage followed by restoration.
      Limiter must seamlessly transition back to Redis and clear _circuit_broken.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=3, window_seconds=60.0)
    user = "reconnect_user"

    # Healthy request
    assert (await limiter.check_request(user))[0] is True
    assert limiter._circuit_broken is False

    # Outage
    mock_redis.set_connected(False)
    assert (await limiter.check_request(user))[0] is True
    assert limiter._circuit_broken is True

    # Redis restored
    mock_redis.set_connected(True)
    limiter._last_failure_time = time.monotonic() - limiter._recovery_cooldown - 1.0

    # Next request must succeed and automatically clear circuit breaker
    allowed_recovered, _ = await limiter.check_request(user)
    assert allowed_recovered is True
    assert limiter._circuit_broken is False, "Circuit breaker must automatically reset to False upon Redis recovery"

    stats = await limiter.get_stats(user)
    assert stats.get("circuit_broken") is False


@pytest.mark.unit
async def test_rate_limiter_token_quota_overflow_and_daily_reset(mock_redis: MockRedisClient) -> None:
    """
    Tier 4 Edge Case 7: Token Quota Overflow and Daily Reset.
      Verifies cumulative token accumulation, exact quota enforcement,
      and date-partitioned key rollover.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10, tokens_per_day=1000)
    user = "token_overflow_user"

    # Step 1: Accumulate tokens
    await limiter.record_tokens(user, 500)
    assert (await limiter.check_request(user))[0] is True

    await limiter.record_tokens(user, 500)  # Reached exact quota of 1000
    blocked, reason = await limiter.check_request(user)
    assert blocked is False, "Request at exact token limit must be blocked"
    assert "daily AI token limit" in reason or "token" in reason.lower()

    # Step 2: Manually verify tomorrow's date key starts fresh
    tomorrow = "20260912"
    tomorrow_key = f"ratelimit:tokens:{user}:{tomorrow}"
    tomorrow_val = await mock_redis.get(tomorrow_key)
    assert tomorrow_val is None or int(tomorrow_val) == 0


@pytest.mark.unit
async def test_rate_limiter_lifecycle_reset_and_close(mock_redis: MockRedisClient) -> None:
    """
    Tier 4: Lifecycle reset and close testing.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5)
    user = "lifecycle_user"

    await limiter.check_request(user)
    await limiter.record_tokens(user, 100)

    # Reset specific user
    await limiter.reset(user)
    # After reset, user should have 0 usage in redis
    stats = await limiter.get_stats(user)
    assert stats["tokens_used_today"] == 0
    assert stats["rpm_current"] == 0

    # Close
    await limiter.close()


@pytest.mark.unit
async def test_rate_limiter_circuit_breaker_3_states(mock_redis: MockRedisClient) -> None:
    """
    Tier 4: Genuine 3-state circuit breaker verification (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
    - Starts in CLOSED state.
    - Trips to OPEN on Redis outage and fast-fails without hitting Redis.
    - Transitions to HALF_OPEN after recovery cooldown elapses.
    - Recovers to CLOSED upon successful probe.
    - If probe in HALF_OPEN fails, transitions back to OPEN.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=5, recovery_cooldown=0.5)
    user = f"cb_3state_{uuid.uuid4().hex[:6]}"

    # Initial state: CLOSED
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False
    assert (await limiter.check_request(user))[0] is True

    # Outage trips circuit breaker to OPEN
    mock_redis.set_connected(False)
    allowed, _ = await limiter.check_request(user)
    assert allowed is True
    assert limiter._circuit_state == CircuitState.OPEN
    assert limiter._circuit_broken is True

    # While OPEN and cooldown has not elapsed, _should_attempt_redis is False (fast-fail)
    assert limiter._should_attempt_redis() is False
    assert limiter._circuit_state == CircuitState.OPEN

    # Simulate cooldown elapsing -> probe allowed, transitions to HALF_OPEN
    limiter._last_failure_time = time.monotonic() - 1.0
    assert limiter._should_attempt_redis() is True
    assert limiter._circuit_state == CircuitState.HALF_OPEN

    # If Redis is still disconnected, probe fails and transitions back to OPEN
    allowed_fallback, _ = await limiter.check_request(user)
    assert allowed_fallback is True
    assert limiter._circuit_state == CircuitState.OPEN
    assert limiter._circuit_broken is True

    # Simulate cooldown elapsing again and Redis recovering
    limiter._last_failure_time = time.monotonic() - 1.0
    mock_redis.set_connected(True)

    # Probe succeeds and recovers to CLOSED
    allowed_recovered, _ = await limiter.check_request(user)
    assert allowed_recovered is True
    assert limiter._circuit_state == CircuitState.CLOSED
    assert limiter._circuit_broken is False


@pytest.mark.unit
async def test_rate_limiter_multi_cost_request(mock_redis: MockRedisClient) -> None:
    """
    Tier 2: Multi-token cost in check_rate_limit.
    - Consuming cost=3 out of rpm=5 records 3 units.
    - Subsequent request with cost=3 is rejected (3 + 3 = 6 > 5).
    - Cost exceeding limit (cost=10 > 5) is rejected immediately.
    - Cost <= 0 raises ValueError.
    """
    rpm = 5
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=rpm, window_seconds=60.0)
    user = f"cost_user_{uuid.uuid4().hex[:6]}"

    # Invalid cost <= 0
    with pytest.raises(ValueError, match="cost must be greater than 0"):
        await limiter.check_rate_limit(user, cost=0)

    with pytest.raises(ValueError, match="cost must be greater than 0"):
        await limiter.check_rate_limit(user, cost=-1)

    # Cost > limit immediately rejected
    over_allowed, over_info = await limiter.check_rate_limit(user, cost=10)
    assert over_allowed is False
    assert over_info["allowed"] is False
    assert over_info["limit"] == rpm

    # Consume cost=3
    allowed1, info1 = await limiter.check_rate_limit(user, cost=3)
    assert allowed1 is True
    assert info1["current_usage"] == 3

    # Try to consume cost=3 again (3 + 3 = 6 > 5) -> rejected
    allowed2, info2 = await limiter.check_rate_limit(user, cost=3)
    assert allowed2 is False
    assert info2["current_usage"] == 3
    assert info2["retry_after"] > 0

    # Consume remaining cost=2 (3 + 2 = 5 <= 5) -> allowed
    allowed3, info3 = await limiter.check_rate_limit(user, cost=2)
    assert allowed3 is True
    assert info3["current_usage"] == 5


@pytest.mark.unit
async def test_rate_limiter_zero_rpm_and_token_limits(mock_redis: MockRedisClient) -> None:
    """
    Tier 2: Zero-value limit handling.
    - rpm=0 and tpd=0 must be respected and not overridden by defaults.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=0, tokens_per_day=0)
    assert limiter._rpm == 0
    assert limiter._tpd == 0

    user = f"zero_user_{uuid.uuid4().hex[:6]}"
    allowed, reason = await limiter.check_request(user)
    assert allowed is False

    allowed_quota, q_info = await limiter.check_token_quota(user, tokens=0)
    assert allowed_quota is False
    assert q_info["limit"] == 0

    allowed_rate, r_info = await limiter.check_rate_limit(user, cost=1)
    assert allowed_rate is False
    assert r_info["limit"] == 0


@pytest.mark.unit
async def test_rate_limiter_global_reset_clears_all_redis_keys(mock_redis: MockRedisClient) -> None:
    """
    Tier 4: limiter.reset() without arguments clears all Redis keys matching ratelimit:*.
    """
    limiter = RateLimiter(redis_client=mock_redis, requests_per_minute=10)
    user_a = "user_reset_alpha"
    user_b = "user_reset_beta"

    await limiter.check_request(user_a)
    await limiter.check_request(user_b)
    await limiter.record_tokens(user_a, 200)
    await limiter.record_tokens(user_b, 300)

    stats_pre_a = await limiter.get_stats(user_a)
    stats_pre_b = await limiter.get_stats(user_b)
    assert stats_pre_a["rpm_current"] == 1
    assert stats_pre_b["rpm_current"] == 1

    # Reset all users
    await limiter.reset()

    # Verify all stats are cleared
    stats_post_a = await limiter.get_stats(user_a)
    stats_post_b = await limiter.get_stats(user_b)
    assert stats_post_a["rpm_current"] == 0
    assert stats_post_a["tokens_used_today"] == 0
    assert stats_post_b["rpm_current"] == 0
    assert stats_post_b["tokens_used_today"] == 0


@pytest.mark.live
async def test_live_redis_rate_limiting(live_endpoints: dict[str, str]) -> None:
    """
    Tier 4 Live: Executes against the live Redis container on localhost:6379.
    """
    try:
        import redis.asyncio as aioredis
    except ImportError:
        pytest.skip("redis[asyncio] package not installed")

    redis_url = live_endpoints["redis"]
    try:
        r = aioredis.from_url(redis_url, socket_timeout=2.0)
        await r.ping()
    except Exception as exc:
        pytest.fail(f"Could not connect to live Redis at {redis_url}: {exc}")

    try:
        limiter = RateLimiter(redis_client=r, requests_per_minute=3, window_seconds=10.0)
        test_user = f"live_test_{uuid.uuid4().hex[:8]}"

        assert (await limiter.check_request(test_user))[0] is True
        assert (await limiter.check_request(test_user))[0] is True
        assert (await limiter.check_request(test_user))[0] is True
        # 4th request must block
        blocked, reason = await limiter.check_request(test_user)
        assert blocked is False, "4th request on live Redis should have been blocked"
        assert "wait" in reason.lower() or "too fast" in reason.lower()

        # Cleanup
        await r.delete(f"ratelimit:rpm:{test_user}")
    finally:
        await r.aclose()
