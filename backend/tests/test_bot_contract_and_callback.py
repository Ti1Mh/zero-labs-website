"""Unit tests for decoupled bot contract via Redis Stream and idempotent callback."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.channels.models import Channel
from app.core.config import get_settings
from app.internal.schemas import PublishResultCallbackRequest
from app.internal.service import handle_publish_callback
from app.main import app
from app.models.callback import ProcessedCallbackEvent
from app.models.content import ContentJob
from app.models.platform import Platform
from app.workers import dispatch_to_bot, publish_content


@pytest.mark.anyio
async def test_worker_dispatches_to_redis_stream():
    """Verify worker produces a structured message to Redis Stream 'publish_jobs' and sets status='dispatched'."""
    job = ContentJob(
        id=555,
        platform_id=1,
        created_by=10,
        description="تست انتشار تلگرام با ردیس استریم",
        status="queued",
        extra_metadata={"title": "تیتر پست", "media_urls": ["https://s3.example.com/img1.jpg"]},
    )
    owner = MagicMock(id=10, owner_user_id=None)
    channel = Channel(
        id=88,
        owner_id=10,
        platform_id=1,
        title="کانال رسمی تلگرام",
        credentials_encrypted="encrypted_blob",
    )
    platform = Platform(id=1, code="telegram", display_name="Telegram")

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock(return_value="1700000000000-0")
    ctx = {"redis": mock_redis}

    with patch("app.workers.session_maker") as mock_session_maker, \
         patch("app.workers.decrypt", return_value="TELEGRAM_BOT_SECRET_TOKEN_ABC"):

        db = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = db

        async def mock_execute(stmt):
            res = MagicMock()
            sql_str = str(stmt)
            if "content_jobs" in sql_str:
                res.scalar_one_or_none.return_value = job
            elif "users" in sql_str:
                res.scalar_one_or_none.return_value = owner
            elif "channels" in sql_str:
                res.scalar_one_or_none.return_value = channel
            elif "platforms" in sql_str:
                res.scalar_one_or_none.return_value = platform
            return res

        db.execute.side_effect = mock_execute

        res = await dispatch_to_bot(ctx=ctx, job_id=555)

        assert res["status"] == "dispatched"
        assert job.status == "dispatched"

        # Verify XADD was called on 'publish_jobs'
        mock_redis.xadd.assert_called_once()
        stream_name, payload = mock_redis.xadd.call_args[0]
        assert stream_name == "publish_jobs"
        assert payload["job_id"] == "555"
        assert payload["platform"] == "telegram"
        assert payload["title"] == "تیتر پست"
        assert payload["description"] == "تست انتشار تلگرام با ردیس استریم"
        assert payload["credentials"] == "TELEGRAM_BOT_SECRET_TOKEN_ABC"
        assert json.loads(payload["media_urls"]) == ["https://s3.example.com/img1.jpg"]
        assert payload["schema_version"] == "1"


@pytest.mark.anyio
async def test_callback_worker_token_authentication():
    """Verify that the callback endpoint strictly enforces WORKER_TOKEN authentication."""
    settings = get_settings()
    token = settings.worker_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Case 1: Missing Authorization Header
        res_no_auth = await client.post(
            "/internal/callback/publish-result",
            json={"event_id": "evt-1", "job_id": 1, "status": "published"},
        )
        assert res_no_auth.status_code == 401

        # Case 2: Invalid Bearer Token
        res_wrong_token = await client.post(
            "/internal/callback/publish-result",
            headers={"Authorization": "Bearer wrong-token-12345"},
            json={"event_id": "evt-1", "job_id": 1, "status": "published"},
        )
        assert res_wrong_token.status_code == 401

        # Case 3: Valid Token with API v1 prefix also works
        with patch("app.internal.router.service.handle_publish_callback") as mock_handler:
            mock_handler.return_value = {
                "status": "processed",
                "job_id": 1,
                "event_id": "evt-1",
                "message": "Job status updated successfully.",
            }
            res_valid = await client.post(
                "/api/v1/internal/callback/publish-result",
                headers={"Authorization": f"Bearer {token}"},
                json={"event_id": "evt-1", "job_id": 1, "status": "published"},
            )
            assert res_valid.status_code == 200
            assert res_valid.json()["status"] == "processed"


@pytest.mark.anyio
async def test_callback_idempotent_processing_and_deduplication():
    """Verify that duplicate event_ids return duplicate_ignored without mutating the job."""
    db = AsyncMock()
    job = ContentJob(
        id=10,
        status="dispatched",
        extra_metadata={},
    )

    req1 = PublishResultCallbackRequest(
        event_id="event-uuid-unique-123",
        job_id=10,
        status="published",
        platform_post_id="tg_msg_9876",
    )

    # First attempt: event not in DB, job is found
    mock_res_empty = MagicMock()
    mock_res_empty.scalar_one_or_none.return_value = None

    mock_res_job = MagicMock()
    mock_res_job.scalar_one_or_none.return_value = job

    db.execute.side_effect = [mock_res_empty, mock_res_job]

    res1 = await handle_publish_callback(db=db, payload=req1)
    assert res1.status == "processed"
    assert job.status == "published"
    assert job.extra_metadata["platform_post_id"] == "tg_msg_9876"
    assert db.add.call_count == 1

    # Second attempt with same event_id: event already in DB
    existing_event = ProcessedCallbackEvent(
        event_id="event-uuid-unique-123",
        job_id=10,
        status="published",
    )
    mock_res_found = MagicMock()
    mock_res_found.scalar_one_or_none.return_value = existing_event
    db.execute.side_effect = [mock_res_found]
    db.add.reset_mock()

    res2 = await handle_publish_callback(db=db, payload=req1)
    assert res2.status == "duplicate_ignored"
    # No new record added and no mutation
    db.add.assert_not_called()


@pytest.mark.anyio
async def test_callback_records_failure_state():
    """Verify callback properly updates ContentJob to failed when bot reports an error."""
    db = AsyncMock()
    job = ContentJob(
        id=20,
        status="dispatched",
        retry_count=1,
    )

    req_fail = PublishResultCallbackRequest(
        event_id="event-fail-999",
        job_id=20,
        status="failed",
        error_message="Telegram API 403: Bot was blocked by the user",
    )

    mock_res_empty = MagicMock()
    mock_res_empty.scalar_one_or_none.return_value = None

    mock_res_job = MagicMock()
    mock_res_job.scalar_one_or_none.return_value = job

    db.execute.side_effect = [mock_res_empty, mock_res_job]

    res = await handle_publish_callback(db=db, payload=req_fail)
    assert res.status == "processed"
    assert job.status == "failed"
    assert job.error_message == "Telegram API 403: Bot was blocked by the user"
    assert job.retry_count == 2
