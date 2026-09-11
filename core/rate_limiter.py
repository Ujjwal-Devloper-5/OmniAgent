"""
Enterprise-grade persistent Redis sliding-window rate limiter.
═══════════════════════════════════════════════════════════════════════════════
Implements Requirement R2 and Acceptance Criterion 2 of Phase 7:
- Persistent Redis sorted set (ZSET) sliding-window algorithm.
- Atomic Lua script for concurrent multi-request synchronization.
- Daily AI token quota tracking with UTC date partitioning and 25-hour TTL.
- Resilient 3-state circuit breaker (CLOSED -> OPEN -> HALF-OPEN/RECOVERY)
  with seamless in-memory sliding-window fallback.
- Dual-interface compatibility:
    1. Chat platform adapters: check_request(user_id), record_tokens(...), get_stats(...)
    2. Enterprise specification (PROJECT.md): check_rate_limit(key, cost), check_token_quota(...)
- Key-level TTL hygiene preventing unbounded memory growth in Redis.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from enum import Enum
import time
from typing import Any, Dict, Optional, Tuple, Union
import uuid

from core.logger import get_logger

log = get_logger(__name__)

# Atomic sliding-window rate limiter Lua script
# Evaluates sliding window eviction, current usage, and conditional member addition in a single atomic step.
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = tostring(ARGV[4])
local cost = tonumber(ARGV[5]) or 1
local clear_before = now - window

-- 1. Evict expired entries outside the sliding window [-inf, now - window]
redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)

-- 2. Count active requests currently in the sliding window
local count = redis.call('ZCARD', key)

-- 3. Check limit
if count + cost <= limit then
    for i = 1, cost do
        redis.call('ZADD', key, now, member .. ':' .. i)
    end
    redis.call('EXPIRE', key, math.ceil(window * 2))
    return {1, limit - (count + cost), 0}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local wait_time = math.ceil(window)
    if #oldest >= 2 then
        wait_time = math.max(1, math.ceil(tonumber(oldest[2]) + window - now))
    end
    return {0, count, wait_time}
end
"""


class CircuitState(str, Enum):
    """3-State Circuit Breaker."""
    CLOSED = "closed"        # Normal Redis operation
    OPEN = "open"            # Redis failed; traffic routed to local fallback
    HALF_OPEN = "half_open"  # Probing Redis recovery


class _MemoryRateLimiter:
    """
    Local in-memory sliding-window and daily token limiter fallback.
    Thread-safe and async-compatible.
    """

    def __init__(self, rpm: int, tpd: int, window: float = 60.0) -> None:
        self.rpm = rpm
        self.tpd = tpd
        self.window = window
        self._windows: Dict[str, list[float]] = {}
        self._tokens: Dict[str, Tuple[str, int]] = {}  # key -> (YYYYMMDD, count)
        self._lock = asyncio.Lock()

    async def check_request(self, user_id: str, now: float) -> Tuple[bool, str]:
        async with self._lock:
            # 1. Check daily token quota
            today = time.strftime("%Y%m%d", time.gmtime(now))
            token_date, tokens_used = self._tokens.get(user_id, (today, 0))
            if token_date != today:
                tokens_used = 0
                self._tokens[user_id] = (today, 0)
            if tokens_used >= self.tpd:
                return False, "📊 You've reached your daily AI token limit. Please try again tomorrow."

            # 2. Check sliding window
            history = self._windows.setdefault(user_id, [])
            cutoff = now - self.window
            self._windows[user_id] = [t for t in history if t > cutoff]
            current_count = len(self._windows[user_id])

            if current_count < self.rpm:
                self._windows[user_id].append(now)
                return True, ""
            else:
                oldest_ts = self._windows[user_id][0] if self._windows[user_id] else now
                wait_time = max(1, int(oldest_ts + self.window - now + 0.999))
                return False, f"⏳ You're sending messages too fast! Please wait {wait_time}s before trying again."

    async def check_rate_limit(self, key: str, cost: int, now: float) -> Tuple[bool, dict]:
        if cost <= 0:
            raise ValueError("cost must be greater than 0")
        async with self._lock:
            history = self._windows.setdefault(key, [])
            cutoff = now - self.window
            self._windows[key] = [t for t in history if t > cutoff]
            current_count = len(self._windows[key])

            if cost > self.rpm:
                return False, {
                    "allowed": False,
                    "current_usage": current_count,
                    "limit": self.rpm,
                    "reset_seconds": int(self.window),
                    "retry_after": int(self.window),
                }

            if current_count + cost <= self.rpm:
                for _ in range(cost):
                    self._windows[key].append(now)
                return True, {
                    "allowed": True,
                    "current_usage": current_count + cost,
                    "limit": self.rpm,
                    "reset_seconds": int(self.window),
                    "retry_after": 0,
                }
            else:
                oldest_ts = self._windows[key][0] if self._windows[key] else now
                wait_time = max(1, int(oldest_ts + self.window - now + 0.999))
                return False, {
                    "allowed": False,
                    "current_usage": current_count,
                    "limit": self.rpm,
                    "reset_seconds": wait_time,
                    "retry_after": wait_time,
                }

    async def record_tokens(self, user_id: str, token_count: int, now: float) -> None:
        async with self._lock:
            today = time.strftime("%Y%m%d", time.gmtime(now))
            token_date, current_tokens = self._tokens.get(user_id, (today, 0))
            if token_date != today:
                current_tokens = 0
            self._tokens[user_id] = (today, current_tokens + token_count)

    async def get_stats(self, user_id: str, now: float) -> Tuple[int, int]:
        async with self._lock:
            history = self._windows.get(user_id, [])
            cutoff = now - self.window
            current_rpm = len([t for t in history if t > cutoff])

            today = time.strftime("%Y%m%d", time.gmtime(now))
            token_date, current_tokens = self._tokens.get(user_id, (today, 0))
            tokens_used = current_tokens if token_date == today else 0
            return current_rpm, tokens_used

    async def reset(self, user_id: Optional[str] = None) -> None:
        async with self._lock:
            if user_id is not None:
                self._windows.pop(user_id, None)
                self._tokens.pop(user_id, None)
            else:
                self._windows.clear()
                self._tokens.clear()


class RateLimiter:
    """
    Production-grade Redis persistent sliding-window rate limiter with circuit breaker.
    """

    def __init__(
        self,
        redis_client_or_rpm: Optional[Union[Any, int]] = None,
        tokens_per_day: Optional[int] = None,
        requests_per_minute: Optional[int] = None,
        redis_client: Optional[Any] = None,
        redis_url: Optional[str] = None,
        window_seconds: float = 60.0,
        recovery_cooldown: float = 1.0,
    ) -> None:
        # Flexible signature supporting both positional redis_client and keyword arguments
        if isinstance(redis_client_or_rpm, int):
            rpm_val = redis_client_or_rpm
            r_client = redis_client
        else:
            rpm_val = requests_per_minute
            r_client = redis_client_or_rpm if redis_client_or_rpm is not None else redis_client

        from config import settings

        if rpm_val is not None:
            self._rpm = rpm_val
        else:
            self._rpm = getattr(settings, "rate_limit_requests_per_minute", 20)

        if tokens_per_day is not None:
            self._tpd = tokens_per_day
        else:
            self._tpd = getattr(settings, "rate_limit_tokens_per_day", 200_000)

        self._window = float(window_seconds)
        self._redis_url = redis_url or getattr(settings, "redis_url", None) or "redis://localhost:6379/0"

        self._redis = r_client
        self._owned_redis = False
        self._redis_init_lock = asyncio.Lock()

        # 3-State Circuit Breaker
        self._circuit_state: CircuitState = CircuitState.CLOSED
        self._circuit_broken: bool = False
        self._last_failure_time: float = 0.0
        self._recovery_cooldown: float = float(recovery_cooldown)

        # In-memory fallback
        self._memory = _MemoryRateLimiter(self._rpm, self._tpd, self._window)

    @staticmethod
    def _token_key(user_id: str, ts: Optional[float] = None) -> str:
        """Construct UTC date-partitioned daily token key."""
        now_ts = ts if ts is not None else time.time()
        today = time.strftime("%Y%m%d", time.gmtime(now_ts))
        return f"ratelimit:tokens:{user_id}:{today}"

    @staticmethod
    def _rpm_key(key: str) -> str:
        """Construct sliding-window RPM sorted set key."""
        return f"ratelimit:rpm:{key}"

    def _should_attempt_redis(self) -> bool:
        """
        Evaluate 3-state circuit breaker.
        - CLOSED: normal Redis operation.
        - OPEN: fast-fail to memory without network calls unless recovery cooldown elapsed.
        - HALF_OPEN: probe Redis recovery.
        """
        if self._circuit_state == CircuitState.CLOSED:
            return True
        if self._circuit_state == CircuitState.HALF_OPEN:
            return True
        now = time.monotonic()
        if (now - self._last_failure_time) > self._recovery_cooldown:
            self._circuit_state = CircuitState.HALF_OPEN
            return True
        return False

    async def _get_redis(self) -> Optional[Any]:
        """Resolve Redis client connection or handle circuit transitions."""
        if self._redis is not None:
            return self._redis

        async with self._redis_init_lock:
            if self._redis is not None:
                return self._redis

            try:
                import redis.asyncio as aioredis
                client = aioredis.from_url(
                    self._redis_url,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    encoding="utf-8",
                    decode_responses=True,
                )
                await client.ping()
                self._redis = client
                self._owned_redis = True
                self._mark_success()
                return self._redis
            except Exception as exc:
                log.warning("Could not establish Redis connection for rate limiting (%s); circuit broken", exc)
                self._mark_failure(exc)
                return None

    def _mark_failure(self, exc: Exception) -> None:
        """Trip circuit breaker to OPEN state on Redis error."""
        self._circuit_state = CircuitState.OPEN
        self._circuit_broken = True
        self._last_failure_time = time.monotonic()
        log.warning("Redis operation failed (%s); degrading to in-memory fallback", exc)

    def _mark_success(self) -> None:
        """Reset circuit breaker to CLOSED state on successful Redis operation."""
        self._circuit_state = CircuitState.CLOSED
        self._circuit_broken = False
        self._last_failure_time = 0.0

    # ── Enterprise Interface (PROJECT.md) ────────────────────────────────────

    async def check_rate_limit(self, key: str, cost: int = 1) -> Tuple[bool, dict]:
        """
        Check sliding-window rate limit for a key.

        Returns
        -------
        (allowed: bool, info: dict)
            info keys: allowed, current_usage, limit, reset_seconds, retry_after
        """
        if cost <= 0:
            raise ValueError("cost must be greater than 0")

        now = time.time()

        if cost > self._rpm:
            wait_time = int(self._window)
            return False, {
                "allowed": False,
                "current_usage": 0,
                "limit": self._rpm,
                "reset_seconds": wait_time,
                "retry_after": wait_time,
            }

        rpm_key = self._rpm_key(key)
        member_id = f"{now}:{uuid.uuid4().hex[:8]}"

        if self._should_attempt_redis():
            redis = await self._get_redis()
            if redis is not None:
                try:
                    res = await redis.eval(
                        SLIDING_WINDOW_LUA,
                        1,
                        rpm_key,
                        str(now),
                        str(self._window),
                        str(self._rpm),
                        member_id,
                        str(cost),
                    )
                    self._mark_success()

                    allowed = (res[0] == 1)
                    if allowed:
                        remaining = int(res[1])
                        current_usage = self._rpm - remaining
                        reset_seconds = int(self._window)
                        retry_after = 0
                    else:
                        current_usage = int(res[1])
                        wait_time = int(res[2])
                        reset_seconds = wait_time
                        retry_after = wait_time

                    return allowed, {
                        "allowed": allowed,
                        "current_usage": current_usage,
                        "limit": self._rpm,
                        "reset_seconds": reset_seconds,
                        "retry_after": retry_after,
                    }
                except Exception as exc:
                    self._mark_failure(exc)

        return await self._memory.check_rate_limit(key, cost, now)

    async def check_token_quota(self, key: str, tokens: int = 0) -> Tuple[bool, dict]:
        """
        Check daily token quota for a key.

        Returns
        -------
        (allowed: bool, info: dict)
            info keys: allowed, tokens_used, limit, remaining, reset_seconds
        """
        now = time.time()
        token_key = self._token_key(key, now)

        # Seconds until next UTC midnight
        now_utc = datetime.now(timezone.utc)
        tomorrow_utc = (now_utc + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        reset_seconds = max(1, int((tomorrow_utc - now_utc).total_seconds()))

        if self._should_attempt_redis():
            redis = await self._get_redis()
            if redis is not None:
                try:
                    raw_val = await redis.get(token_key)
                    self._mark_success()

                    used = int(raw_val) if raw_val is not None else 0
                    allowed = (used + tokens) <= self._tpd if tokens > 0 else (used < self._tpd)
                    remaining = max(0, self._tpd - used)

                    return allowed, {
                        "allowed": allowed,
                        "tokens_used": used,
                        "limit": self._tpd,
                        "remaining": remaining,
                        "reset_seconds": reset_seconds,
                    }
                except Exception as exc:
                    self._mark_failure(exc)

        # In-memory fallback
        _, memory_used = await self._memory.get_stats(key, now)
        allowed = (memory_used + tokens) <= self._tpd if tokens > 0 else (memory_used < self._tpd)
        remaining = max(0, self._tpd - memory_used)
        return allowed, {
            "allowed": allowed,
            "tokens_used": memory_used,
            "limit": self._tpd,
            "remaining": remaining,
            "reset_seconds": reset_seconds,
        }

    # ── Adapter Facade (Discord, Telegram, Slack Bots) ────────────────────────

    async def check_request(self, user_id: str) -> Tuple[bool, str]:
        """
        Unified check invoked by bot adapters.
        1. Checks daily token quota
        2. Checks sliding-window RPM limit

        Returns
        -------
        (allowed: bool, reason: str)
        """
        now = time.time()

        # Step 1: Check daily token quota
        token_allowed, token_info = await self.check_token_quota(user_id, tokens=0)
        if not token_allowed:
            log.warning("Rate limit hit (daily tokens) for user %s", user_id)
            return False, "📊 You've reached your daily AI token limit. Please try again tomorrow."

        # Step 2: Check sliding-window RPM
        rpm_allowed, rpm_info = await self.check_rate_limit(user_id, cost=1)
        if not rpm_allowed:
            wait_time = rpm_info.get("retry_after", int(self._window))
            log.warning("Rate limit hit (RPM) for user %s (wait %ds)", user_id, wait_time)
            return False, f"⏳ You're sending messages too fast! Please wait {wait_time}s before trying again."

        return True, ""

    async def record_tokens(self, user_id: str, token_count: int) -> None:
        """Record token usage post-generation with 25-hour TTL."""
        if token_count <= 0:
            return

        now = time.time()
        token_key = self._token_key(user_id, now)

        if self._should_attempt_redis():
            redis = await self._get_redis()
            if redis is not None:
                try:
                    pipe = redis.pipeline() if hasattr(redis, "pipeline") else None
                    if pipe is not None:
                        pipe.incrby(token_key, token_count)
                        pipe.expire(token_key, 90_000)
                        await pipe.execute()
                    else:
                        await redis.incrby(token_key, token_count)
                        await redis.expire(token_key, 90_000)
                    self._mark_success()
                    return
                except Exception as exc:
                    self._mark_failure(exc)

        # In-memory fallback
        await self._memory.record_tokens(user_id, token_count, now)

    async def get_stats(self, user_id: str) -> dict:
        """Fetch current rate limit status for user."""
        now = time.time()
        token_key = self._token_key(user_id, now)
        rpm_key = self._rpm_key(user_id)

        used_tokens = 0
        current_rpm = 0

        if self._should_attempt_redis():
            redis = await self._get_redis()
            if redis is not None:
                try:
                    clear_before = now - self._window
                    try:
                        await redis.zremrangebyscore(rpm_key, "-inf", clear_before)
                    except Exception:
                        pass
                    current_rpm = await redis.zcard(rpm_key)
                    raw_tokens = await redis.get(token_key)
                    used_tokens = int(raw_tokens) if raw_tokens is not None else 0
                    self._mark_success()
                except Exception as exc:
                    self._mark_failure(exc)
                    current_rpm, used_tokens = await self._memory.get_stats(user_id, now)
            else:
                current_rpm, used_tokens = await self._memory.get_stats(user_id, now)
        else:
            current_rpm, used_tokens = await self._memory.get_stats(user_id, now)

        remaining_rpm = max(0, self._rpm - current_rpm)
        return {
            "requests_remaining_this_minute": remaining_rpm,
            "requests_per_minute_limit": self._rpm,
            "tokens_used_today": used_tokens,
            "tokens_per_day_limit": self._tpd,
            "active_users_tracked": 1,
            # Extended Phase 7 metrics & test contracts:
            "rpm_limit": self._rpm,
            "rpm_current": current_rpm,
            "daily_token_limit": self._tpd,
            "daily_tokens_used": used_tokens,
            "circuit_broken": self._circuit_broken,
            "circuit_state": self._circuit_state.value,
        }

    # ── Management & Lifecycle ───────────────────────────────────────────────

    async def reset(self, user_id: Optional[str] = None) -> None:
        """Reset rate limit data (useful in tests and administrative tasks)."""
        await self._memory.reset(user_id)

        if self._should_attempt_redis():
            redis = await self._get_redis()
            if redis is not None:
                try:
                    if user_id:
                        today = time.strftime("%Y%m%d", time.gmtime())
                        keys_to_delete = [f"ratelimit:rpm:{user_id}", f"ratelimit:tokens:{user_id}:{today}"]
                        if hasattr(redis, "keys"):
                            token_keys = await redis.keys(f"ratelimit:tokens:{user_id}:*")
                            keys_to_delete.extend(token_keys)
                        await redis.delete(*set(keys_to_delete))
                    else:
                        if hasattr(redis, "keys"):
                            matching = await redis.keys("ratelimit:*")
                            if matching:
                                await redis.delete(*matching)
                        elif hasattr(redis, "scan_iter"):
                            matching = []
                            async for k in redis.scan_iter("ratelimit:*"):
                                matching.append(k)
                            if matching:
                                await redis.delete(*matching)
                    self._mark_success()
                except Exception as exc:
                    self._mark_failure(exc)

    async def close(self) -> None:
        """Safely close underlying Redis connection if owned."""
        if self._redis is not None and self._owned_redis:
            try:
                if hasattr(self._redis, "aclose"):
                    await self._redis.aclose()
                elif hasattr(self._redis, "close"):
                    await self._redis.close()
            except Exception as exc:
                log.warning("Error closing Redis connection: %s", exc)
            finally:
                self._redis = None


# Module-level singleton
_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or initialize the module-level RateLimiter singleton."""
    global _limiter
    if _limiter is None:
        from config import settings
        _limiter = RateLimiter(
            requests_per_minute=getattr(settings, "rate_limit_requests_per_minute", 20),
            tokens_per_day=getattr(settings, "rate_limit_tokens_per_day", 200_000),
            redis_url=getattr(settings, "redis_url", None),
        )
    return _limiter
