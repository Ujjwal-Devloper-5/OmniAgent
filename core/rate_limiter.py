"""
Per-user token-bucket rate limiter.
- Enforces per-minute request limits and per-day token budgets.
- Includes a TTL-based bucket eviction to prevent memory leaks from
  accumulating inactive users indefinitely.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

from core.logger import get_logger

log = get_logger(__name__)

# Evict buckets for users who have been inactive for this many seconds (24h)
_BUCKET_TTL_SECONDS = 86_400


@dataclass
class _UserBucket:
    # ── Per-minute request counter ──
    minute_tokens: float = 0.0
    last_minute_refill: float = field(default_factory=time.monotonic)

    # ── Per-day token counter ──
    daily_tokens_used: int = 0
    day_start: float = field(default_factory=time.time)

    # ── For TTL eviction ──
    last_seen: float = field(default_factory=time.monotonic)


class RateLimiter:
    """
    Thread-safe, async-compatible token-bucket rate limiter.

    Parameters
    ----------
    requests_per_minute : int
        Max number of messages a user can send per minute.
    tokens_per_day : int
        Max AI tokens (approximate) a user can consume per day.
    """

    def __init__(
        self,
        requests_per_minute: int = 20,
        tokens_per_day: int = 50_000,
    ) -> None:
        self._rpm = requests_per_minute
        self._tpd = tokens_per_day
        self._buckets: Dict[str, _UserBucket] = {}
        self._lock = asyncio.Lock()
        self._last_eviction = time.monotonic()

    def _get_bucket(self, user_id: str) -> _UserBucket:
        """Get or create a bucket for this user, starting fully topped up."""
        if user_id not in self._buckets:
            self._buckets[user_id] = _UserBucket(minute_tokens=float(self._rpm))
        return self._buckets[user_id]

    def _evict_stale_buckets(self) -> None:
        """
        Remove buckets for users inactive for > TTL seconds.
        Called periodically to prevent unbounded memory growth.
        """
        now = time.monotonic()
        # Only run eviction at most once per hour
        if now - self._last_eviction < 3600:
            return
        stale = [
            uid
            for uid, bucket in self._buckets.items()
            if now - bucket.last_seen > _BUCKET_TTL_SECONDS
        ]
        for uid in stale:
            del self._buckets[uid]
        if stale:
            log.info("Rate limiter evicted %d stale user buckets", len(stale))
        self._last_eviction = now

    async def check_request(self, user_id: str) -> Tuple[bool, str]:
        """
        Check if a request is allowed.

        Returns
        -------
        (allowed, reason)
            allowed : bool — True if request should proceed.
            reason  : str  — Human-readable reason if denied.
        """
        async with self._lock:
            self._evict_stale_buckets()
            bucket = self._get_bucket(user_id)
            now_mono = time.monotonic()
            now_time = time.time()

            # ── Update last seen ──────────────────────────────────────────────
            bucket.last_seen = now_mono

            # ── Refill per-minute bucket ──────────────────────────────────────
            elapsed = now_mono - bucket.last_minute_refill
            bucket.minute_tokens = min(
                self._rpm,
                bucket.minute_tokens + elapsed * (self._rpm / 60.0),
            )
            bucket.last_minute_refill = now_mono

            if bucket.minute_tokens < 1:
                wait_secs = (1 - bucket.minute_tokens) * (60.0 / self._rpm)
                log.warning("Rate limit hit (RPM) for user %s", user_id)
                return False, (
                    f"⏳ You're sending messages too fast! "
                    f"Please wait {wait_secs:.0f}s before trying again."
                )

            # ── Daily token budget ────────────────────────────────────────────
            if now_time - bucket.day_start >= 86400:
                bucket.daily_tokens_used = 0
                bucket.day_start = now_time

            if bucket.daily_tokens_used >= self._tpd:
                log.warning("Rate limit hit (daily tokens) for user %s", user_id)
                return False, (
                    "📊 You've reached your daily AI token limit. "
                    "Please try again tomorrow."
                )

            bucket.minute_tokens -= 1
            return True, ""

    async def record_tokens(self, user_id: str, token_count: int) -> None:
        """Record token usage after a successful response."""
        async with self._lock:
            bucket = self._get_bucket(user_id)
            bucket.daily_tokens_used += token_count
            bucket.last_seen = time.monotonic()

    async def get_stats(self, user_id: str) -> dict:
        """Return current rate-limit stats for a user."""
        async with self._lock:
            bucket = self._get_bucket(user_id)
            return {
                "requests_remaining_this_minute": int(bucket.minute_tokens),
                "requests_per_minute_limit": self._rpm,
                "tokens_used_today": bucket.daily_tokens_used,
                "tokens_per_day_limit": self._tpd,
                "active_users_tracked": len(self._buckets),
            }


# Module-level singleton (initialised on first use)
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        from config import settings

        _limiter = RateLimiter(
            requests_per_minute=settings.rate_limit_requests_per_minute,
            tokens_per_day=settings.rate_limit_tokens_per_day,
        )
    return _limiter
