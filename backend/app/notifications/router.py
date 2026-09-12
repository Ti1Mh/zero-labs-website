"""API router for in-app notifications and real-time SSE stream."""

import asyncio
import json
import logging
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt as pyjwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TeamContext, get_current_team
from app.auth.models import User
from app.auth.security import decode_access_token
from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.notifications.service import (
    list_user_notifications,
    mark_all_notifications_as_read,
    mark_notification_as_read,
)
from app.schemas.notification import (
    MarkReadResponse,
    NotificationListResponse,
    NotificationOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])
_bearer = HTTPBearer(auto_error=False)


@router.get("", response_model=NotificationListResponse)
async def list_notifications_endpoint(
    is_read: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    """List notifications for the current authenticated user."""
    items, unread_count, total = await list_user_notifications(
        db=db,
        user_id=team.current_user.id,
        is_read=is_read,
        limit=limit,
        offset=offset,
    )
    return NotificationListResponse(
        notifications=[NotificationOut.model_validate(n) for n in items],
        unread_count=unread_count,
        total=total,
    )


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_single_read_endpoint(
    notification_id: int,
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> NotificationOut:
    """Mark a single notification as read."""
    notification = await mark_notification_as_read(
        db=db,
        notification_id=notification_id,
        user_id=team.current_user.id,
    )
    return NotificationOut.model_validate(notification)


@router.post("/read-all", response_model=MarkReadResponse)
async def mark_all_read_endpoint(
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    """Mark all unread notifications as read for current user."""
    count = await mark_all_notifications_as_read(db=db, user_id=team.current_user.id)
    return MarkReadResponse(success=True, marked_count=count)


@router.get("/stream")
async def stream_notifications_endpoint(
    request: Request,
    token: str | None = Query(default=None),
    max_messages: int | None = Query(
        default=None,
        description="Optional limit on messages before closing stream (for testing and controlled polling)",
    ),
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Server-Sent Events (SSE) endpoint for real-time notification push."""
    raw_token = credentials.credentials if credentials else token
    if not raw_token:
        raise AuthenticationError("احراز هویت برای دریافت استریم اعلان‌ها الزامی است.")
    try:
        user_id = decode_access_token(raw_token)
    except pyjwt.InvalidTokenError as exc:
        raise AuthenticationError("توکن معتبر نیست.") from exc

    user_res = await db.execute(select(User).where(User.id == user_id))
    user = user_res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("کاربر معتبر نیست.")

    async def event_generator():
        delivered = 0
        yield f"event: connected\ndata: {json.dumps({'user_id': user_id, 'status': 'connected'})}\n\n"
        delivered += 1
        if max_messages is not None and delivered >= max_messages:
            return

        redis_pool = getattr(request.app.state, "redis", None)
        if redis_pool is None or not hasattr(redis_pool, "pubsub"):
            while not await request.is_disconnected():
                for _ in range(30):
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.5)
                yield ": ping\n\n"
                delivered += 1
                if max_messages is not None and delivered >= max_messages:
                    return
            return


        pubsub = redis_pool.pubsub()
        channels = [f"notifications:{user_id}", "notifications:broadcast"]
        await pubsub.subscribe(*channels)
        try:
            while not await request.is_disconnected():
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=0.5
                    )
                    if msg is not None and msg.get("type") == "message":
                        data = msg.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        yield f"event: notification\ndata: {data}\n\n"
                    else:
                        yield ": ping\n\n"
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(f"Error reading SSE pubsub message: {exc}")
                    yield ": ping\n\n"
                for _ in range(4):
                    if await request.is_disconnected():
                        break
                    await asyncio.sleep(0.5)
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            except Exception:
                pass


    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
