"""Unit tests verifying Claude's security audit fixes and production readiness."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.requests import Request

from app.ai.schemas import GeneratePostRequest, GeneratedPostResponse
from app.ai.service import generate_post
from app.auth.models import OtpCode, User
from app.auth.router import _ip
from app.auth.security import hash_otp_code
from app.auth.service import MAX_OTP_ATTEMPTS, _consume_otp
from app.core.config import Settings
from app.core.exceptions import AuthenticationError, InvalidInputError, RateLimitError
from app.core.rate_limit import enforce_otp_verify_rate_limits, _memory_store


@pytest.mark.anyio
async def test_otp_brute_force_counter_persists_and_locks_out():
    """Verify that failed attempts commit to DB immediately and lock out after MAX_OTP_ATTEMPTS."""
    now = datetime.now(timezone.utc)
    otp = OtpCode(
        id=1,
        phone_number="09121112233",
        purpose="register",
        code_hash=hash_otp_code("123456"),
        attempts=0,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
    )

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = otp
    db.execute.return_value = mock_result

    # 1. Fail 5 times with incorrect code
    for i in range(1, MAX_OTP_ATTEMPTS + 1):
        with pytest.raises(AuthenticationError, match="کد نادرست است."):
            await _consume_otp(db, "09121112233", "register", "999999")
        assert otp.attempts == i
        # Crucial check: commit MUST have been called to persist attempt count before raising
        assert db.commit.call_count == i

    # 2. On 6th attempt (even with CORRECT code "123456"), user is locked out
    with pytest.raises(AuthenticationError, match="تعداد تلاش‌ها تمام شد"):
        await _consume_otp(db, "09121112233", "register", "123456")


@pytest.mark.anyio
async def test_otp_verify_rate_limiting_phone_and_ip():
    """Verify sliding window rate limiting on OTP verification (5/min phone, 15/min IP)."""
    _memory_store.clear()
    phone = "09129998877"
    ip = "198.51.100.22"

    # First 5 calls pass
    for _ in range(5):
        await enforce_otp_verify_rate_limits(redis_client=None, phone=phone, ip_address=ip)

    # 6th call for the same phone triggers RateLimitError
    with pytest.raises(RateLimitError, match="تعداد تلاش‌های تأیید کد برای این شماره"):
        await enforce_otp_verify_rate_limits(redis_client=None, phone=phone, ip_address=ip)

    # Different phone on same IP can continue up to IP limit (15 total)
    _memory_store.pop(f"rate_limit:otp:verify:phone:{phone}", None)
    for i in range(10):  # Already did 5 on this IP, 10 more makes 15
        await enforce_otp_verify_rate_limits(redis_client=None, phone=f"091299988{i:02d}", ip_address=ip)

    # 16th call on same IP triggers IP RateLimitError
    with pytest.raises(RateLimitError, match="تعداد تلاش‌های تأیید کد از این آدرس اینترنتی"):
        await enforce_otp_verify_rate_limits(redis_client=None, phone="09120000099", ip_address=ip)


def test_cors_fail_fast_in_production():
    """Verify Settings rejects localhost/127.0.0.1 origins when debug=False."""
    # 1. Insecure localhost with debug=False => FAILS FAST
    with pytest.raises(ValidationError, match="Insecure CORS origin"):
        Settings(
            debug=False,
            cors_origins_raw="http://localhost:3000",
            secret_key="test-secret-key-for-jwt-signing-hs256",
            database_url="sqlite+aiosqlite:///:memory:",
        )

    # 2. Insecure 127.0.0.1 with debug=False => FAILS FAST
    with pytest.raises(ValidationError, match="Insecure CORS origin"):
        Settings(
            debug=False,
            cors_origins_raw="http://127.0.0.1:8000,https://app.mezonflow.ir",
            secret_key="test-secret-key-for-jwt-signing-hs256",
            database_url="sqlite+aiosqlite:///:memory:",
        )

    # 3. Localhost with debug=True => ALLOWED in local dev
    s_dev = Settings(
        debug=True,
        cors_origins_raw="http://localhost:3000",
        secret_key="test-secret-key-for-jwt-signing-hs256",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert "http://localhost:3000" in s_dev.cors_origins

    # 4. Production domain with debug=False => ALLOWED in production
    s_prod = Settings(
        debug=False,
        cors_origins_raw="https://app.mezonflow.ir,https://admin.mezonflow.ir",
        secret_key="test-secret-key-for-jwt-signing-hs256",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert s_prod.cors_origins == ["https://app.mezonflow.ir", "https://admin.mezonflow.ir"]


def test_ip_extraction_spoofing_defense():
    """Verify _ip() extracts X-Real-IP or the rightmost hop of X-Forwarded-For."""
    # 1. X-Real-IP takes precedence
    req1 = MagicMock(spec=Request)
    req1.headers = Headers({"x-real-ip": "203.0.113.1", "x-forwarded-for": "10.0.0.1, 10.0.0.2"})
    assert _ip(req1) == "203.0.113.1"

    # 2. X-Forwarded-For uses rightmost hop (closest trusted reverse proxy)
    req2 = MagicMock(spec=Request)
    req2.headers = Headers({"x-forwarded-for": "spoofed.client.ip, intermediate.proxy, 203.0.113.99"})
    assert _ip(req2) == "203.0.113.99"

    # 3. Fallback to direct client host
    req3 = MagicMock(spec=Request)
    req3.headers = Headers({})
    req3.client.host = "192.0.2.1"
    assert _ip(req3) == "192.0.2.1"


@pytest.mark.anyio
async def test_worker_logs_no_token_leak():
    """Verify workers log channel_id and do NOT log decrypted tokens."""
    from app.channels.models import Channel
    from app.models.content import ContentJob
    from app.workers import publish_content

    job = ContentJob(
        id=777,
        platform_id=1,
        created_by=10,
        status="queued",
        max_retries=3,
        retry_count=0,
    )
    owner = User(id=10, phone_number="09121112233", owner_user_id=None)
    channel = Channel(
        id=42,
        owner_id=10,
        platform_id=1,
        title="test_channel",
        credentials_encrypted="encrypted_mock_blob",
    )

    with patch("app.workers.session_maker") as mock_session_maker, \
         patch("app.workers.decrypt", return_value="SUPER_SECRET_OAUTH_ACCESS_TOKEN_XYZ123"), \
         patch("app.workers.logger.info") as mock_logger_info:

        db = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = db

        # Mock query sequence: job, user, channel
        async def mock_execute(stmt):
            res = MagicMock()
            sql_str = str(stmt)
            if "content_jobs" in sql_str:
                res.scalar_one_or_none.return_value = job
            elif "users" in sql_str:
                res.scalar_one_or_none.return_value = owner
            elif "channels" in sql_str:
                res.scalar_one_or_none.return_value = channel
            return res

        db.execute.side_effect = mock_execute

        await publish_content(ctx={}, job_id=777)

        # Inspect log message
        mock_logger_info.assert_called_once()
        log_message = mock_logger_info.call_args[0][0]
        assert "channel_id=42" in log_message
        assert "SUPER_SECRET" not in log_message
        assert "token=" not in log_message


@pytest.mark.anyio
async def test_ai_output_safety_filter():
    """Verify that unsafe text generated by AI model triggers InvalidInputError on output."""
    db = AsyncMock()
    team = MagicMock()
    team.owner.id = 1
    team.current_user.id = 1

    req = GeneratePostRequest(
        platform="telegram",
        topic="کسب درآمد آسان با هوش مصنوعی",
        tone="professional",
    )

    # Mock provider returning gambling/phishing text with prohibited phrase 'کازینو آنلاین'
    unsafe_response = GeneratedPostResponse(
        headline="ورود به کازینو آنلاین و برد میلیونی",
        hook="همین حالا شروع کنید",
        body="بهترین روش برای تفریح و درآمد ثبت نام در کازینو آنلاین است.",
        hashtags=["#کازینو"],
        model_used="google/gemini-2.0-flash:free",
        provider_used="mock",
    )

    with patch("app.ai.service.get_ai_provider"), \
         patch("app.ai.service.resolve_user_ai_tier", return_value=("free", "google/gemini-2.0-flash:free")), \
         patch("app.ai.service.resolve_active_persona", return_value=None), \
         patch("app.ai.service._execute_with_retry", return_value=unsafe_response):

        with pytest.raises(InvalidInputError, match="محتوای تولید شده توسط هوش مصنوعی مغایر با قوانین ایمنی است"):
            await generate_post(db=db, team=team, request=req)


@pytest.mark.anyio
async def test_healthz_endpoint_probe():
    """Verify /healthz endpoint performs live DB and Redis checks."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code in [200, 503]
        data = res.json()
        assert "status" in data
        assert "database" in data
        assert "redis" in data
