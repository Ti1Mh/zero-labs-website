"""Comprehensive tests for Redis Sliding Window Rate Limiting and AI Spend Circuit Breaker."""

import asyncio
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai.ledger_models import AIUsageLedger
from app.ai.schemas import GeneratePostRequest
from app.auth.dependencies import TeamContext, get_current_user, require
from app.auth.models import Role, User
from app.auth.schemas import RegisterRequest, ResetRequest
from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import RateLimitError
from app.core.rate_limit import (
    _memory_store,
    check_ai_spend_circuit_breaker,
    check_sliding_window_rate_limit,
    enforce_ai_burst_limit,
    enforce_otp_rate_limits,
    enforce_rate_limit,
    record_ai_spend_cache,
)
from app.main import app


# --- 1. Sliding Window Core Engine Unit Tests ---

def test_sliding_window_in_memory_allowed_and_blocked():
    """Verify in-memory fallback sliding window allows up to cap and blocks subsequent requests."""
    async def _run():
        _memory_store.clear()
        test_key = "test:sliding_window:user1"

        # Limit: 3 requests per 10 seconds
        res1, retry1, rem1 = await check_sliding_window_rate_limit(None, test_key, max_requests=3, window_seconds=10)
        assert res1 is True
        assert retry1 == 0
        assert rem1 == 2

        res2, retry2, rem2 = await check_sliding_window_rate_limit(None, test_key, max_requests=3, window_seconds=10)
        assert res2 is True
        assert rem2 == 1

        res3, retry3, rem3 = await check_sliding_window_rate_limit(None, test_key, max_requests=3, window_seconds=10)
        assert res3 is True
        assert rem3 == 0

        # 4th request must be rejected
        res4, retry4, rem4 = await check_sliding_window_rate_limit(None, test_key, max_requests=3, window_seconds=10)
        assert res4 is False
        assert retry4 > 0
        assert rem4 == 0

        # Enforce helper raises RateLimitError
        with pytest.raises(RateLimitError) as exc_info:
            await enforce_rate_limit(None, test_key, max_requests=3, window_seconds=10)
        assert exc_info.value.retry_after == retry4

    asyncio.run(_run())


def test_sliding_window_with_mock_redis():
    """Verify Redis Sorted Set commands (zremrangebyscore, zcard, zadd, expire) are invoked."""
    async def _run():
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore.return_value = 0
        mock_redis.zcard.return_value = 1
        mock_redis.zadd.return_value = 1
        mock_redis.expire.return_value = True

        allowed, retry_after, remaining = await check_sliding_window_rate_limit(
            redis_client=mock_redis,
            key="test:redis:window",
            max_requests=5,
            window_seconds=60,
        )
        assert allowed is True
        assert remaining == 3
        mock_redis.zremrangebyscore.assert_called_once()
        mock_redis.zcard.assert_called_once()
        mock_redis.zadd.assert_called_once()

    asyncio.run(_run())


# --- 2. Multi-Tier OTP Protection Tests ---

def test_otp_multi_tier_rate_limits():
    """Verify OTP enforcement handles 60s cooldown, 10m cap, and IP abuse defense."""
    async def _run():
        _memory_store.clear()
        phone = "09121112233"
        ip = "192.168.1.50"

        # 1. First request -> passes
        await enforce_otp_rate_limits(None, phone=phone, ip_address=ip)

        # 2. Second request immediately -> fails on 60s cooldown
        with pytest.raises(RateLimitError) as exc_cooldown:
            await enforce_otp_rate_limits(None, phone=phone, ip_address=ip)
        assert "۶۰ ثانیه" in str(exc_cooldown.value)
        assert exc_cooldown.value.retry_after is not None

        # 3. Simulate passing of cooldown by clearing cooldown key only
        _memory_store[f"rate_limit:otp:phone:cooldown:{phone}"] = []
        await enforce_otp_rate_limits(None, phone=phone, ip_address=ip)

        # 4. Third request: clear cooldown again, should pass
        _memory_store[f"rate_limit:otp:phone:cooldown:{phone}"] = []
        await enforce_otp_rate_limits(None, phone=phone, ip_address=ip)

        # 5. Fourth request: 10m burst cap exceeded (max 3 per 10m)
        _memory_store[f"rate_limit:otp:phone:cooldown:{phone}"] = []
        with pytest.raises(RateLimitError) as exc_10m:
            await enforce_otp_rate_limits(None, phone=phone, ip_address=ip)
        assert "۱۰ دقیقه" in str(exc_10m.value)

        # 6. Test IP Botnet protection: 10 requests from same IP with different phone numbers
        _memory_store.clear()
        attacker_ip = "185.220.101.5"
        for i in range(10):
            await enforce_otp_rate_limits(None, phone=f"0912999000{i}", ip_address=attacker_ip)

        # 11th request from attacker IP must be rejected regardless of phone number
        with pytest.raises(RateLimitError) as exc_ip:
            await enforce_otp_rate_limits(None, phone="09129990099", ip_address=attacker_ip)
        assert "آدرس" in str(exc_ip.value)

    asyncio.run(_run())


# --- 3. HTTP 429 & Retry-After Header Tests ---

def test_auth_otp_endpoints_return_429_and_retry_after():
    """Verify FastAPI returns HTTP 429 with standard Retry-After header on rapid OTP requests."""
    async def _run():
        _memory_store.clear()
        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one.return_value = 0

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[get_db] = lambda: mock_db
            app.state.redis = None  # test with in-memory sliding window fallback

            try:
                # Mock SMS sending in auth service
                with patch("app.auth.service.get_sms_sender"):
                    # 1. First register request -> 200 OK
                    payload = {"phone": "09123334455"}
                    resp1 = await client.post("/api/v1/auth/register/request", json=payload)
                    assert resp1.status_code == 200
                    assert resp1.json()["message"] == "کد تأیید ارسال شد."

                    # 2. Second register request immediately -> 429 Too Many Requests
                    resp2 = await client.post("/api/v1/auth/register/request", json=payload)
                    assert resp2.status_code == 429
                    assert "Retry-After" in resp2.headers
                    retry_after_val = int(resp2.headers["Retry-After"])
                    assert retry_after_val > 0
                    assert resp2.json()["code"] == "RATE_LIMITED"
                    assert resp2.json()["retry_after"] == retry_after_val

                    # 3. Password reset endpoint rate limiting
                    reset_payload = {"phone": "09127778899"}
                    resp_reset1 = await client.post("/api/v1/auth/password-reset/request", json=reset_payload)
                    assert resp_reset1.status_code == 200

                    resp_reset2 = await client.post("/api/v1/auth/password-reset/request", json=reset_payload)
                    assert resp_reset2.status_code == 429
                    assert "Retry-After" in resp_reset2.headers
            finally:
                app.dependency_overrides.clear()

    asyncio.run(_run())


# --- 4. AI Burst Rate Limiter Tests ---

def test_ai_burst_rate_limiter():
    """Verify AI per-user burst limiter blocks rapid calls beyond 10 req/min."""
    async def _run():
        _memory_store.clear()
        user_id = 42

        # 10 requests pass
        for _ in range(10):
            await enforce_ai_burst_limit(None, user_id=user_id)

        # 11th request raises RateLimitError
        with pytest.raises(RateLimitError) as exc_burst:
            await enforce_ai_burst_limit(None, user_id=user_id)
        assert "سرعت ارسال درخواست" in str(exc_burst.value)
        assert exc_burst.value.retry_after is not None

        # Different user should not be affected
        await enforce_ai_burst_limit(None, user_id=99)

    asyncio.run(_run())


# --- 5. AI Spend Circuit Breaker Tests ---

def test_ai_spend_circuit_breaker_db_and_cache():
    """Verify circuit breaker halts requests when daily spend exceeds threshold."""
    async def _run():
        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        owner_id = 7

        # Case A: Spend under cap (e.g. 250 cents < 500 cents cap) -> passes
        mock_redis.get.return_value = "250"
        await check_ai_spend_circuit_breaker(mock_redis, mock_db, owner_id=owner_id)

        # Case B: Spend exceeds cap in Redis cache (e.g. 520 cents >= 500 cents cap) -> tripped
        mock_redis.get.return_value = "520"
        with pytest.raises(RateLimitError) as exc_trip:
            await check_ai_spend_circuit_breaker(mock_redis, mock_db, owner_id=owner_id)
        assert "Spend Circuit Breaker" in str(exc_trip.value)
        assert exc_trip.value.retry_after == 3600

        # Case C: Redis cache miss -> queries DB aggregation
        mock_redis.get.return_value = None
        mock_db.execute.return_value.scalar.return_value = 600  # DB returns 600 cents ($6.00)

        with pytest.raises(RateLimitError) as exc_db_trip:
            await check_ai_spend_circuit_breaker(mock_redis, mock_db, owner_id=owner_id)
        assert "سقف هزینه روزانه" in str(exc_db_trip.value)
        mock_db.execute.assert_called_once()
        mock_redis.set.assert_called_once_with(f"spend:daily:{owner_id}", "600", ex=300)

        # Case D: Updating spend cache after generation
        mock_redis.reset_mock()
        mock_redis.ttl.return_value = 1000
        await record_ai_spend_cache(mock_redis, owner_id=owner_id, cost_cents=15)
        mock_redis.incrby.assert_called_once_with(f"spend:daily:{owner_id}", 15)

    asyncio.run(_run())


# --- 6. AI Endpoint Guardrails Integration Test ---

def test_ai_generate_endpoint_guardrail_integration():
    """Verify calling /api/v1/ai/generate triggers guardrails (burst and circuit breaker)."""
    async def _run():
        _memory_store.clear()
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        user = User(id=10, phone_number="09120001111", password_hash="hash", is_active=True, is_verified=True)
        role = Role(name="Owner", actions=["*"])
        team = TeamContext(owner=user, current_user=user, role=role, actions={"*"}, scope=[])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[get_db] = lambda: mock_db
            app.dependency_overrides[get_current_user] = lambda: user
            app.dependency_overrides[require("ai:use")] = lambda: team
            app.state.redis = mock_redis

            try:
                # Simulate daily spend tripped
                mock_redis.get.return_value = "550"
                mock_redis.zremrangebyscore.return_value = 0
                mock_redis.zcard.return_value = 1
                mock_redis.zadd.return_value = 1
                mock_redis.expire.return_value = True

                payload = {
                    "topic": "نکات بازاریابی دیجیتال در اینستاگرام",
                    "platform": "instagram",
                }
                resp = await client.post("/api/v1/ai/generate", json=payload)
                assert resp.status_code == 429
                assert "Spend Circuit Breaker" in resp.json()["message"]
                assert "Retry-After" in resp.headers
                assert resp.json()["retry_after"] == 3600
            finally:
                app.dependency_overrides.clear()

    asyncio.run(_run())
