"""Pydantic schemas for notifications."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    """Schema for a single user notification."""

    id: int
    user_id: int | None
    type: str
    title: str
    message: str
    is_read: bool
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Paginated or listed response of user notifications."""

    notifications: list[NotificationOut]
    unread_count: int
    total: int


class MarkReadResponse(BaseModel):
    """Response confirming notifications were marked as read."""

    success: bool
    marked_count: int
