"""Distributed Sliding Window Rate Limiting and AI Spend Circuit Breaker engine."""

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import logging
import math
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ledger_models import AIUsageLedger
from app.core.config import get_settings
from app.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# Thread/Task-safe in-memory fallback store when Redis is unavailable
_memory_store: dict[str, list[float]] = {}
_memory_lock = asyncio.Lock()


async def check_sliding_window_rate_limit(
    redis_client: Any,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """Check sliding window rate limit using Redis Sorted Set or in-memory fallback.

    Returns:
        (is_allowed, retry_after_seconds, remaining_requests)
    """
    now = time.time()
    clear_before = now - window_seconds

    # --- 1. Redis Implementation ---
    if redis_client is not None:
        try:
            # We wrap the operations in a Redis pipeline or atomic sequence
            # Remove timestamps outside the sliding window
            rem_coro = redis_client.zremrangebyscore(key, "-inf", str(clear_before))
            if inspect.isawaitable(rem_coro):
                await rem_coro

            # Count valid requests in current window
            card_coro = redis_client.zcard(key)
            if inspect.isawaitable(card_coro):
                card_coro = await card_coro
            current_count = int(card_coro or 0)

            if current_count < max_requests:
                member = f"{now}-{uuid4().hex[:6]}"
                add_coro = redis_client.zadd(key, {member: now})
                if inspect.isawaitable(add_coro):
                    await add_coro

                expire_coro = redis_client.expire(key, int(window_seconds) + 2)
                if inspect.isawaitable(expire_coro):
                    await expire_coro

                remaining = max(0, max_requests - current_count - 1)
                return True, 0, remaining
            else:
                # Limit reached: find oldest request timestamp to calculate precise retry_after
                range_coro = redis_client.zrange(key, 0, 0, withscores=True)
                if inspect.isawaitable(range_coro):
                    range_coro = await range_coro

                retry_after = 1
                if range_coro:
                    oldest_score = float(range_coro[0][1])
                    retry_after = max(1, math.ceil(oldest_score + window_seconds - now))

                return False, retry_after, 0
        except Exception as exc:
            logger.warning(f"Redis rate limit check failed for {key}, falling back to memory: {exc}")

    # --- 2. In-Memory Fallback Implementation ---
    async with _memory_lock:
        timestamps = _memory_store.get(key, [])
        # Filter out timestamps outside sliding window
        valid_timestamps = [t for t in timestamps if t > clear_before]

        if len(valid_timestamps) < max_requests:
            valid_timestamps.append(now)
            _memory_store[key] = valid_timestamps
            remaining = max(0, max_requests - len(valid_timestamps))
            return True, 0, remaining
        else:
            oldest_time = valid_timestamps[0]
            retry_after = max(1, math.ceil(oldest_time + window_seconds - now))
            _memory_store[key] = valid_timestamps
            return False, retry_after, 0


async def enforce_rate_limit(
    redis_client: Any,
    key: str,
    max_requests: int,
    window_seconds: int,
    error_message: str = "تعداد درخواست بیش از حد مجاز است.",
) -> None:
    """Check sliding window and raise RateLimitError with retry_after if limit exceeded."""
    allowed, retry_after, _ = await check_sliding_window_rate_limit(
        redis_client=redis_client,
        key=key,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
    if not allowed:
        raise RateLimitError(error_message, retry_after=retry_after)


# --- Specialized Multi-Tier OTP Rate Limiters ---

async def enforce_otp_rate_limits(
    redis_client: Any,
    phone: str,
    ip_address: str | None = None,
) -> None:
    """Enforce strict multi-tier rate limiting on OTP requests before database work.

    1. Phone Cooldown: 1 request per 60s
    2. Phone 10m Window: 3 requests per 600s
    3. Phone 1h Window: 5 requests per 3600s
    4. IP 10m Window: 10 requests per 600s
    """
    settings = get_settings()

    # Tier 1: 60-second Cooldown
    await enforce_rate_limit(
        redis_client=redis_client,
        key=f"rate_limit:otp:phone:cooldown:{phone}",
        max_requests=1,
        window_seconds=settings.otp_cooldown_seconds,
        error_message="لطفاً قبل از درخواست مجدد کد، ۶۰ ثانیه صبر کنید.",
    )

    # Tier 2: 10-Minute Burst Cap
    await enforce_rate_limit(
        redis_client=redis_client,
        key=f"rate_limit:otp:phone:10m:{phone}",
        max_requests=settings.otp_window_10m_cap,
        window_seconds=600,
        error_message="تعداد درخواست کد تأیید در ۱۰ دقیقه بیش از حد مجاز است.",
    )

    # Tier 3: 1-Hour Cap
    await enforce_rate_limit(
        redis_client=redis_client,
        key=f"rate_limit:otp:phone:1h:{phone}",
        max_requests=settings.otp_hourly_cap,
        window_seconds=3600,
        error_message="سقف مجاز ساعتی درخواست کد برای این شماره تکمیل شده است.",
    )

    # Tier 4: IP Address Botnet Defense
    if ip_address:
        await enforce_rate_limit(
            redis_client=redis_client,
            key=f"rate_limit:otp:ip:10m:{ip_address}",
            max_requests=settings.otp_ip_10m_cap,
            window_seconds=600,
            error_message="تعداد درخواست کد از این آدرس اینترنتی بیش از حد مجاز است.",
        )


# --- Specialized AI Burst Limiter & Spend Circuit Breaker ---

async def enforce_ai_burst_limit(redis_client: Any, user_id: int) -> None:
    """Enforce per-user burst rate limiting on AI generation (e.g. 10 req/min)."""
    settings = get_settings()
    await enforce_rate_limit(
        redis_client=redis_client,
        key=f"rate_limit:ai:user:burst:{user_id}",
        max_requests=settings.ai_burst_per_minute,
        window_seconds=60,
        error_message="سرعت ارسال درخواست به هوش مصنوعی بیش از حد مجاز است؛ لطفاً چند لحظه صبر کنید.",
    )


async def check_ai_spend_circuit_breaker(
    redis_client: Any,
    db: AsyncSession,
    owner_id: int,
) -> None:
    """Check daily aggregated AI spend against safety tripwire ($5.00/day default)."""
    settings = get_settings()
    spend_key = f"spend:daily:{owner_id}"
    daily_spend: int | None = None

    if redis_client is not None:
        try:
            cached = redis_client.get(spend_key)
            if inspect.isawaitable(cached):
                cached = await cached
            if cached is not None:
                daily_spend = int(cached)
        except Exception as exc:
            logger.warning(f"Failed to read cached daily spend from Redis: {exc}")

    # If cache miss, aggregate from immutable AIUsageLedger for the last 24 hours
    if daily_spend is None:
        since = datetime.now(timezone.utc) - timedelta(days=1)
        stmt = (
            select(func.coalesce(func.sum(AIUsageLedger.cost_cents), 0))
            .where(
                AIUsageLedger.owner_id == owner_id,
                AIUsageLedger.created_at >= since,
            )
        )
        res = await db.execute(stmt)
        scalar = res.scalar()
        if inspect.isawaitable(scalar):
            scalar = await scalar
        daily_spend = int(scalar or 0)

        # Cache in Redis with 5-minute TTL
        if redis_client is not None:
            try:
                set_coro = redis_client.set(spend_key, str(daily_spend), ex=300)
                if inspect.isawaitable(set_coro):
                    await set_coro
            except Exception as exc:
                logger.warning(f"Failed to cache daily spend in Redis: {exc}")

    if daily_spend >= settings.ai_daily_spend_cap_cents:
        raise RateLimitError(
            "سقف هزینه روزانه مصرف هوش مصنوعی برای حساب شما فعال شده است (Spend Circuit Breaker). "
            "جهت افزایش سقف، لطفاً با پشتیبانی تماس بگیرید یا فردا تلاش فرمایید.",
            retry_after=3600,
        )


async def record_ai_spend_cache(
    redis_client: Any,
    owner_id: int,
    cost_cents: int,
) -> None:
    """Increment cached daily spend in Redis after generation."""
    if redis_client is None or cost_cents <= 0:
        return

    spend_key = f"spend:daily:{owner_id}"
    try:
        incr_coro = redis_client.incrby(spend_key, cost_cents)
        if inspect.isawaitable(incr_coro):
            await incr_coro

        # Ensure TTL is set
        ttl_coro = redis_client.ttl(spend_key)
        if inspect.isawaitable(ttl_coro):
            ttl_coro = await ttl_coro
        if int(ttl_coro or -1) < 0:
            exp_coro = redis_client.expire(spend_key, 86400)
            if inspect.isawaitable(exp_coro):
                await exp_coro
    except Exception as exc:
        logger.warning(f"Failed to increment daily spend cache: {exc}")
