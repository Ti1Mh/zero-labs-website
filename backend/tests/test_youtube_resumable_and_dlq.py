"""Tests for YouTube constraints, Resumable Upload protocol, COPPA guardrails, and DLQ architecture."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.admin.schemas import DLQJobListResponse, RetryJobResponse
from app.admin.service import list_dlq_jobs_admin, retry_dlq_job_admin
from app.ai.safety import check_coppa_consistency
from app.ai.youtube_schemas import YouTubePostContent, enforce_youtube_limits
from app.auth.dependencies import get_current_user, require_superuser
from app.auth.models import User
from app.channels.youtube_uploader import (
    YouTubeResumableUploader,
    calculate_exponential_backoff,
    is_youtube_short,
)
from app.core.database import get_db
from app.core.exceptions import AuthorizationError, NotFoundError
from app.main import app
from app.models.content import ContentJob
from app.workers import publish_content


# --- 1. YouTube LLM Constraints & Fallback Truncation Tests ---

def test_youtube_post_content_schema_valid():
    """Verify valid YouTube metadata passes Pydantic validation."""
    data = {
        "title": "۱۰ ترفند سئو در سال ۲۰۲۶ برای رشد پیج",
        "description": "در این ویدیو به بررسی بهترین روش‌های سئو می‌پردازیم.",
        "tags": ["سئو", "آموزش سئو", "یوتیوب", "دیجیتال مارکتینگ"],
        "is_short": False,
        "made_for_kids": False,
    }
    model = YouTubePostContent(**data)
    assert model.title == data["title"]
    assert len(model.tags) == 4
    assert model.is_short is False


def test_youtube_tags_auto_truncation_under_500_chars():
    """Tags validator should discard tags that push total comma-separated length beyond 500 chars."""
    many_tags = [f"tag_number_{i:03d}_very_long_keyword" for i in range(50)]
    model = YouTubePostContent(
        title="تست تگ‌ها",
        description="توضیحات تست",
        tags=many_tags,
    )
    total_len = sum(len(t) for t in model.tags) + max(0, len(model.tags) - 1)
    assert total_len <= 500
    assert len(model.tags) < len(many_tags)


def test_enforce_youtube_limits_word_boundary_truncation():
    """Deterministic fallback should cleanly truncate titles > 100 chars at word boundaries."""
    long_title = "این یک عنوان تستی به شدت طولانی برای ویدیو در یوتیوب است که قصد داریم رفتار کوتاه سازی روی مرز کلمات را تست کنیم و طولانی تر شود"
    assert len(long_title) > 100

    content = {
        "title": long_title,
        "description": "توضیح تستی",
        "tags": ["تگ۱", "تگ۲"],
        "is_short": True,
    }
    enforced = enforce_youtube_limits(content)

    assert len(enforced["title"]) <= 100
    assert enforced["title"].endswith("...")
    # Shorts tag should be appended to description if is_short is True
    assert "#Shorts" in enforced["description"]


def test_enforce_youtube_limits_description_and_tags():
    """Fallback truncates descriptions > 5000 chars and enforces tag length ceiling."""
    giant_desc = "توضیحات طولانی " * 500
    assert len(giant_desc) > 5000

    many_tags = [f"keyword_{i}" for i in range(100)]

    content = {
        "title": "عنوان استاندارد",
        "description": giant_desc,
        "tags": many_tags,
        "is_short": False,
    }
    enforced = enforce_youtube_limits(content)

    assert len(enforced["description"]) <= 5000
    assert enforced["description"].endswith("...")

    total_tag_len = sum(len(t) for t in enforced["tags"]) + max(0, len(enforced["tags"]) - 1)
    assert total_tag_len <= 500


# --- 2. COPPA Consistency Guardrail Tests ---

def test_coppa_consistency_advisory_warning():
    """Detect child/youth themes when made_for_kids is False and emit advisory warning."""
    # Theme without made_for_kids flag
    is_safe, warning = check_coppa_consistency(
        "کارتون شاد کودکانه و آموزش نقاشی برای کودکان و خردسالان",
        made_for_kids=False,
    )
    assert is_safe is False
    assert warning is not None
    assert "madeForKids" in warning

    # Declared properly as made_for_kids
    is_safe, warning = check_coppa_consistency(
        "کارتون شاد کودکانه و آموزش نقاشی برای کودکان و خردسالان",
        made_for_kids=True,
    )
    assert is_safe is True
    assert warning is None

    # General tech content
    is_safe, warning = check_coppa_consistency(
        "آموزش پیشرفته داکر و کوبرنتیز در محیط پروداکشن",
        made_for_kids=False,
    )
    assert is_safe is True
    assert warning is None


# --- 3. Google Resumable Upload Protocol & Video Utilities Tests ---

def test_exponential_backoff_and_shorts_detection():
    """Verify exponential backoff with jitter and Shorts format detection."""
    # Exponential backoff
    delay_0 = calculate_exponential_backoff(0, base_delay=1.0, max_delay=60.0)
    delay_3 = calculate_exponential_backoff(3, base_delay=1.0, max_delay=60.0)
    assert 1.0 <= delay_0 <= 2.0
    assert 8.0 <= delay_3 <= 13.0

    # Shorts format detection
    assert is_youtube_short(width=1080, height=1920, duration_seconds=45.0) is True
    assert is_youtube_short(width=1080, height=1080, duration_seconds=59.0) is True  # Square
    assert is_youtube_short(width=1920, height=1080, duration_seconds=45.0) is False  # Landscape
    assert is_youtube_short(width=1080, height=1920, duration_seconds=250.0) is False  # Too long for Shorts


def test_youtube_resumable_session_initiation():
    """Verify initiate_upload_session extracts the Google Location session header."""
    async def _run():
        uploader = YouTubeResumableUploader(access_token="mock_token_123")
        mock_client = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=resumable_abc123"
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        session_url = await uploader.initiate_upload_session(
            title="ویدیوی آزمایشی",
            description="توضیحات ویدیوی آزمایشی",
            tags=["یوتیوب", "تست"],
            file_size=10485760,
            client=mock_client,
        )

        assert session_url == "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=resumable_abc123"
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert "snippet" in kwargs["json"]
        assert kwargs["json"]["snippet"]["title"] == "ویدیوی آزمایشی"
        assert kwargs["headers"]["X-Upload-Content-Length"] == "10485760"

    asyncio.run(_run())


def test_youtube_resumable_chunk_upload_and_status_query():
    """Verify HTTP 308 Resume Incomplete handling and chunk progression."""
    async def _run():
        uploader = YouTubeResumableUploader(access_token="mock_token_123")
        mock_client = AsyncMock()

        # 1. Chunk 1: Server returns HTTP 308 (Resume Incomplete, bytes 0-5242879)
        mock_resp_308 = MagicMock()
        mock_resp_308.status_code = 308
        mock_resp_308.headers = {"Range": "bytes=0-5242879"}
        mock_client.put.return_value = mock_resp_308

        chunk_data = b"X" * 5242880
        is_done, video_meta, next_offset = await uploader.upload_chunk(
            session_url="http://upload-session",
            chunk_data=chunk_data,
            start_byte=0,
            file_size=10485760,
            client=mock_client,
        )
        assert is_done is False
        assert video_meta is None
        assert next_offset == 5242880

        # 2. Status query on disconnect: returns next byte expected
        mock_query_resp = MagicMock()
        mock_query_resp.status_code = 308
        mock_query_resp.headers = {"Range": "bytes=0-5242879"}
        mock_client.put.return_value = mock_query_resp

        queried_offset = await uploader.query_upload_status(
            session_url="http://upload-session",
            file_size=10485760,
            client=mock_client,
        )
        assert queried_offset == 5242880

        # 3. Final Chunk: Server returns HTTP 200 OK
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {"id": "youtube_vid_999", "snippet": {"title": "تست"}}
        mock_client.put.return_value = mock_resp_200

        is_done, video_meta, next_offset = await uploader.upload_chunk(
            session_url="http://upload-session",
            chunk_data=chunk_data,
            start_byte=5242880,
            file_size=10485760,
            client=mock_client,
        )
        assert is_done is True
        assert video_meta["id"] == "youtube_vid_999"
        assert next_offset == 10485760

    asyncio.run(_run())


# --- 4. Worker Resilience & Dead Letter Queue (DLQ) Tests ---

def test_worker_retry_and_dlq_transition():
    """Verify worker retries transient failures and transitions to failed/DLQ after max retries."""
    async def _run():
        mock_redis = AsyncMock()
        ctx = {"redis": mock_redis}

        # Scenario A: Transient failure when retry_count < max_retries
        job_transient = ContentJob(
            id=101,
            description="محتوای تستی",
            platform_id=1,
            created_by=1,
            status="queued",
            retry_count=1,
            max_retries=3,
        )

        with patch("app.workers.session_maker") as mock_maker:
            mock_db = AsyncMock()
            mock_maker.return_value.__aenter__.return_value = mock_db
            # Query returns job, but channel raises exception (simulating transient network error)
            mock_db.execute.return_value.scalar_one_or_none.side_effect = [
                job_transient,  # Job fetch
                None,           # User owner
                None,           # Channel not found -> raises RuntimeError
            ]

            res = await publish_content(ctx, job_id=101)
            assert res["status"] == "queued"
            assert job_transient.retry_count == 2
            assert job_transient.error_message is not None
            # Verified that re-enqueue was scheduled in Redis
            mock_redis.enqueue_job.assert_called_once()
            args, kwargs = mock_redis.enqueue_job.call_args
            assert args == ("publish_content", 101)
            assert "_defer_by" in kwargs

        # Scenario B: Exhausted retries -> transition to failed (DLQ)
        mock_redis.reset_mock()
        job_exhausted = ContentJob(
            id=102,
            description="محتوای پایانی",
            platform_id=1,
            created_by=1,
            status="queued",
            retry_count=3,
            max_retries=3,
        )

        with patch("app.workers.session_maker") as mock_maker:
            mock_db = AsyncMock()
            mock_maker.return_value.__aenter__.return_value = mock_db
            mock_db.execute.return_value.scalar_one_or_none.side_effect = [
                job_exhausted,
                None,
                None,
            ]

            res = await publish_content(ctx, job_id=102)
            assert res["status"] == "failed"
            assert job_exhausted.retry_count == 4
            assert job_exhausted.traceback_log is not None
            # No further re-enqueue should happen
            mock_redis.enqueue_job.assert_not_called()

    asyncio.run(_run())


# --- 5. Super Admin DLQ Management & Replay Endpoint Tests ---

def test_admin_dlq_service_and_replay():
    """Verify list_dlq_jobs_admin and retry_dlq_job_admin."""
    async def _run():
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        failed_job = ContentJob(
            id=55,
            platform_id=2,
            description="پست شکست خورده",
            status="failed",
            error_message="Connection refused",
            traceback_log="Traceback ...",
            retry_count=5,
            max_retries=5,
            last_attempt_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            extra_metadata={},
        )

        # 1. Test list_dlq_jobs_admin
        mock_db.execute.return_value.scalar.return_value = 1
        mock_db.execute.return_value.scalars.return_value.all.return_value = [failed_job]

        dlq_list = await list_dlq_jobs_admin(mock_db, limit=10, offset=0)
        assert dlq_list.total == 1
        assert len(dlq_list.items) == 1
        assert dlq_list.items[0].id == 55
        assert dlq_list.items[0].status == "failed"

        # 2. Test retry_dlq_job_admin
        mock_db.execute.return_value.scalar_one_or_none.return_value = failed_job
        retry_res = await retry_dlq_job_admin(mock_db, job_id=55, redis_pool=mock_redis)

        assert retry_res.job_id == 55
        assert retry_res.status == "queued"
        assert failed_job.status == "queued"
        assert failed_job.retry_count == 0
        assert failed_job.error_message is None
        mock_redis.enqueue_job.assert_called_once_with("publish_content", 55)

        # 3. Test retry with non-existent job raises NotFoundError
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        with pytest.raises(NotFoundError):
            await retry_dlq_job_admin(mock_db, job_id=999)

    asyncio.run(_run())


def test_admin_dlq_api_endpoints():
    """Verify Super Admin HTTP routes for DLQ listing and job replaying."""
    async def _run():
        superuser = User(
            id=99,
            phone_number="09129999999",
            password_hash="hash",
            is_active=True,
            is_superuser=True,
        )
        mock_db = AsyncMock()
        mock_redis = AsyncMock()

        failed_job = ContentJob(
            id=77,
            platform_id=1,
            description="پست مرده در صف",
            status="failed",
            error_message="YouTube Quota Exceeded",
            traceback_log="Traceback ...",
            retry_count=5,
            max_retries=5,
            last_attempt_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            extra_metadata={},
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[get_current_user] = lambda: superuser
            app.dependency_overrides[require_superuser] = lambda: superuser
            app.dependency_overrides[get_db] = lambda: mock_db
            app.state.redis = mock_redis

            try:
                # 1. GET /api/v1/admin/jobs/dlq
                mock_db.execute.return_value.scalar.return_value = 1
                mock_db.execute.return_value.scalars.return_value.all.return_value = [failed_job]

                resp = await client.get("/api/v1/admin/jobs/dlq")
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == 1
                assert data["items"][0]["id"] == 77
                assert data["items"][0]["error_message"] == "YouTube Quota Exceeded"

                # 2. POST /api/v1/admin/jobs/77/retry
                mock_db.execute.return_value.scalar_one_or_none.return_value = failed_job
                resp_retry = await client.post("/api/v1/admin/jobs/77/retry")
                assert resp_retry.status_code == 200
                retry_data = resp_retry.json()
                assert retry_data["job_id"] == 77
                assert retry_data["status"] == "queued"
                mock_redis.enqueue_job.assert_called_once_with("publish_content", 77)
            finally:
                app.dependency_overrides.clear()

    asyncio.run(_run())
