"""Service layer for in-app notification management and real-time broadcasting."""

from datetime import datetime, timezone
import inspect
import json
import logging
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.notification import Notification

logger = logging.getLogger(__name__)


async def send_notification(
    db: AsyncSession,
    user_id: int | None,
    type: str,
    title: str,
    message: str,
    metadata_json: dict[str, Any] | None = None,
    redis_client: Any = None,
) -> Notification:
    """Create a persistent notification and broadcast it via Redis Pub/Sub."""
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        metadata_json=metadata_json or {},
        is_read=False,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    # Broadcast via Redis Pub/Sub if available
    if redis_client is not None:
        try:
            payload = json.dumps(
                {
                    "id": notification.id,
                    "user_id": notification.user_id,
                    "type": notification.type,
                    "title": notification.title,
                    "message": notification.message,
                    "is_read": notification.is_read,
                    "metadata_json": notification.metadata_json,
                    "created_at": notification.created_at.isoformat()
                    if notification.created_at
                    else datetime.now(timezone.utc).isoformat(),
                }
            )
            # Publish to user-specific channel and broadcast channel if global
            channel = f"notifications:{user_id}" if user_id is not None else "notifications:broadcast"
            coro = redis_client.publish(channel, payload)
            if inspect.isawaitable(coro):
                await coro
        except Exception as exc:
            logger.warning(f"Failed to broadcast notification via Redis Pub/Sub: {exc}")

    return notification


async def list_user_notifications(
    db: AsyncSession,
    user_id: int,
    is_read: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """List notifications for a user (including system broadcasts) with pagination and unread count."""
    base_filter = or_(Notification.user_id == user_id, Notification.user_id.is_(None))

    # Total matching notifications
    total_query = select(func.count(Notification.id)).where(base_filter)
    if is_read is not None:
        total_query = total_query.where(Notification.is_read == is_read)
    total_res = await db.execute(total_query)
    total = total_res.scalar_one() or 0

    # Total unread notifications
    unread_query = select(func.count(Notification.id)).where(
        and_(base_filter, Notification.is_read.is_(False))
    )
    unread_res = await db.execute(unread_query)
    unread_count = unread_res.scalar_one() or 0

    # Fetch items
    query = (
        select(Notification)
        .where(base_filter)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if is_read is not None:
        query = query.where(Notification.is_read == is_read)

    res = await db.execute(query)
    items = list(res.scalars().all())

    return items, unread_count, total


async def mark_notification_as_read(
    db: AsyncSession,
    notification_id: int,
    user_id: int,
) -> Notification:
    """Mark a single notification as read."""
    query = select(Notification).where(
        Notification.id == notification_id,
        or_(Notification.user_id == user_id, Notification.user_id.is_(None)),
    )
    res = await db.execute(query)
    notification = res.scalar_one_or_none()
    if notification is None:
        raise NotFoundError("اعلان مورد نظر یافت نشد.")

    notification.is_read = True
    await db.commit()
    await db.refresh(notification)
    return notification


async def mark_all_notifications_as_read(
    db: AsyncSession,
    user_id: int,
) -> int:
    """Mark all unread notifications for a user as read."""
    stmt = (
        update(Notification)
        .where(
            or_(Notification.user_id == user_id, Notification.user_id.is_(None)),
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    res = await db.execute(stmt)
    await db.commit()
    return res.rowcount or 0
