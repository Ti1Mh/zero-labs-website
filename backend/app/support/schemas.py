"""Pydantic schemas for Support Desk tickets and conversation threads."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class CreateTicketRequest(BaseModel):
    """Payload to create a new customer support ticket."""

    subject: str = Field(..., min_length=3, max_length=200, description="Summary of the inquiry or issue")
    category: str = Field(default="general", max_length=50, description="e.g. billing, technical, ai, general")
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    initial_message: str = Field(..., min_length=1, max_length=10000, description="Detailed problem description")
    attachments: list[dict[str, Any]] = Field(default_factory=list, description="MinIO file upload descriptors")


class ReplyTicketRequest(BaseModel):
    """Payload to append a reply or internal note to a ticket thread."""

    content: str = Field(..., min_length=1, max_length=10000)
    is_internal_note: bool = Field(
        default=False,
        description="Whether this message is an internal staff note (visible only to admins)",
    )
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class UpdateTicketStatusRequest(BaseModel):
    """Payload to transition ticket lifecycle status."""

    status: str = Field(..., pattern="^(open|in_progress|waiting_user|closed)$")


class UpdateTicketPriorityRequest(BaseModel):
    """Payload to adjust ticket urgency."""

    priority: str = Field(..., pattern="^(low|medium|high|critical)$")


class TicketMessageResponse(BaseModel):
    """A message entry in a ticket discussion thread."""

    id: int | None = None
    ticket_id: int | None = None
    sender_id: int
    is_staff: bool = False
    is_internal_note: bool = False
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TicketListItemResponse(BaseModel):
    """Summary view of a ticket for list tables."""

    id: int | None = None
    user_id: int
    subject: str
    category: str = "general"
    priority: str = "medium"
    status: str = "open"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class TicketDetailResponse(BaseModel):
    """Full ticket view including filtered message thread."""

    id: int | None = None
    user_id: int
    subject: str
    category: str = "general"
    priority: str = "medium"
    status: str = "open"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[TicketMessageResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
