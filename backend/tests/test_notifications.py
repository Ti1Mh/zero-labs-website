"""Tests for the real-time Notification Engine, SSE stream, and worker DLQ alerts."""

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from app.auth.dependencies import TeamContext, get_current_team
from app.auth.models import User
from app.auth.security import create_access_token
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.main import app
from app.models.content import ContentJob
from app.models.notification import Notification
from app.notifications.service import (
    list_user_notifications,
    mark_all_notifications_as_read,
    mark_notification_as_read,
    send_notification,
)
from app.notifications.router import router as notification_router


@pytest.mark.anyio
async def test_send_notification_service_and_redis_broadcast():
    """Verify send_notification persists record to DB and publishes to Redis Pub/Sub."""
    mock_db = AsyncMock()
    mock_redis = AsyncMock()

    created = await send_notification(
        db=mock_db,
        user_id=42,
        type="job_failed",
        title="شکست در انتشار",
        message="ویدیو در یوتیوب به دلیل حجم بالا رد شد.",
        metadata_json={"job_id": 100, "error": "file_too_large"},
        redis_client=mock_redis,
    )

    assert created.user_id == 42
    assert created.type == "job_failed"
    assert created.title == "شکست در انتشار"
    assert created.is_read is False
    assert created.metadata_json == {"job_id": 100, "error": "file_too_large"}

    mock_db.add.assert_called_once_with(created)
    mock_db.commit.assert_awaited_once()
    mock_db.refresh.assert_awaited_once_with(created)

    mock_redis.publish.assert_awaited_once()
    channel_arg, payload_arg = mock_redis.publish.call_args[0]
    assert channel_arg == "notifications:42"
    parsed_payload = json.loads(payload_arg)
    assert parsed_payload["type"] == "job_failed"
    assert parsed_payload["user_id"] == 42


@pytest.mark.anyio
async def test_notification_service_mark_read_and_not_found():
    """Verify mark_notification_as_read toggles is_read and raises NotFoundError appropriately."""
    mock_db = AsyncMock()

    # Case 1: Found
    existing = Notification(
        id=7,
        user_id=42,
        type="info",
        title="خوش‌آمدید",
        message="به مزون‌فلو خوش آمدید.",
        is_read=False,
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = existing
    mock_db.execute.return_value = mock_res

    updated = await mark_notification_as_read(mock_db, notification_id=7, user_id=42)
    assert updated.is_read is True
    mock_db.commit.assert_awaited_once()

    # Case 2: Not found
    mock_db.reset_mock()
    mock_res_none = MagicMock()
    mock_res_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_res_none

    with pytest.raises(NotFoundError):
        await mark_notification_as_read(mock_db, notification_id=999, user_id=42)


@pytest.mark.anyio
async def test_notification_service_mark_all_read():
    """Verify mark_all_notifications_as_read updates rows and returns modified count."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 4
    mock_db.execute.return_value = mock_result

    count = await mark_all_notifications_as_read(mock_db, user_id=42)
    assert count == 4
    mock_db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_notification_api_list_and_mark_read():
    """Verify GET /notifications and PATCH /notifications/{id}/read HTTP endpoints."""
    mock_user = User(
        id=42,
        phone_number="+989121112233",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    team_ctx = TeamContext(
        owner=mock_user,
        current_user=mock_user,
        role=None,
        actions={"*"},
        scope=[],
    )

    notif1 = Notification(
        id=1,
        user_id=42,
        type="alert",
        title="هشدار اول",
        message="پیام تستی ۱",
        is_read=False,
        metadata_json={},
        created_at=datetime.now(timezone.utc),
    )

    mock_db = AsyncMock()

    app.dependency_overrides[get_current_team] = lambda: team_ctx
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        # Test GET /notifications
        with patch("app.notifications.router.list_user_notifications") as mock_list:
            mock_list.return_value = ([notif1], 1, 1)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                res = await client.get("/api/v1/notifications")
                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 1
                assert data["unread_count"] == 1
                assert len(data["notifications"]) == 1
                assert data["notifications"][0]["title"] == "هشدار اول"

        # Test PATCH /notifications/{id}/read
        with patch("app.notifications.router.mark_notification_as_read") as mock_mark:
            read_notif = Notification(
                id=1,
                user_id=42,
                type="alert",
                title="هشدار اول",
                message="پیام تستی ۱",
                is_read=True,
                metadata_json={},
                created_at=datetime.now(timezone.utc),
            )
            mock_mark.return_value = read_notif

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                patch_res = await client.patch("/api/v1/notifications/1/read")
                assert patch_res.status_code == 200
                assert patch_res.json()["is_read"] is True

        # Test POST /notifications/read-all
        with patch("app.notifications.router.mark_all_notifications_as_read") as mock_mark_all:
            mock_mark_all.return_value = 5

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                post_res = await client.post("/api/v1/notifications/read-all")
                assert post_res.status_code == 200
                assert post_res.json() == {"success": True, "marked_count": 5}

    finally:
        app.dependency_overrides.pop(get_current_team, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_notification_sse_stream_handshake():
    """Verify GET /notifications/stream returns text/event-stream and initial handshake."""
    mock_user = User(
        id=42,
        phone_number="+989121112233",
        is_active=True,
        is_verified=True,
    )

    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_user
    mock_db.execute.return_value = mock_res

    token = create_access_token(user_id=42)
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Query param token authentication with max_messages=1 to cleanly terminate ASGI stream
            async with client.stream("GET", f"/api/v1/notifications/stream?token={token}&max_messages=1") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                assert response.headers.get("x-accel-buffering") == "no"

                # Read first SSE chunk
                first_chunk = b""
                async for chunk in response.aiter_bytes():
                    first_chunk += chunk
                    if b"event: connected" in first_chunk:
                        break

                assert b"event: connected" in first_chunk
                assert b"status" in first_chunk
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_worker_dlq_triggers_notification():
    """Verify that when a worker permanently fails a job (DLQ), send_notification is invoked."""
    from app.workers import publish_content

    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}

    job_exhausted = ContentJob(
        id=202,
        description="پست خراب با خطای دائمی",
        platform_id=1,
        created_by=42,
        status="queued",
        retry_count=4,
        max_retries=4,
    )

    with patch("app.workers.session_maker") as mock_maker:
        mock_db = AsyncMock()
        mock_maker.return_value.__aenter__.return_value = mock_db
        # Job query returns job_exhausted, subsequent calls simulate channel fetch error
        mock_db.execute.return_value.scalar_one_or_none.side_effect = [
            job_exhausted,
            None,
            None,
        ]

        with patch("app.notifications.service.send_notification", new_callable=AsyncMock) as mock_notify:
            res = await publish_content(ctx, job_id=202)
            assert res["status"] == "failed"
            assert job_exhausted.retry_count == 5
            mock_notify.assert_awaited_once()
            call_kwargs = mock_notify.call_args.kwargs
            assert call_kwargs["user_id"] == 42
            assert call_kwargs["type"] == "job_failed"
            assert "202" in call_kwargs["title"]
            assert call_kwargs["metadata_json"]["job_id"] == 202


