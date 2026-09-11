"""Pydantic schemas for Super Admin backoffice endpoints."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


# --- Plan Management Schemas ---

class AdminPlanCreate(BaseModel):
    """Schema to create a new subscription plan via Super Admin."""

    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=50, pattern="^[a-z0-9-_]+$")
    description: str | None = None
    prices: dict[str, Any] = Field(
        default_factory=dict,
        description="Currency pricing map e.g. {'IRR': {'monthly': 490000, 'yearly': 4900000}, 'USD': {'monthly': 15, 'yearly': 150}}",
    )
    monthly_quota: int = Field(default=0, ge=0)
    features: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AdminPlanUpdate(BaseModel):
    """Schema to update an existing subscription plan via Super Admin."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = None
    prices: dict[str, Any] | None = None
    monthly_quota: int | None = Field(default=None, ge=0)
    features: dict[str, Any] | None = None
    is_active: bool | None = None


class AdminPlanResponse(BaseModel):
    """Admin view of a subscription plan."""

    id: int | None = None
    name: str
    slug: str
    description: str | None = None
    prices: dict[str, Any] = Field(default_factory=dict)
    monthly_quota: int = 0
    features: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- User Management Schemas ---

class AdminUserItem(BaseModel):
    """Summary of a user for the Admin user table."""

    id: int
    phone_number: str
    email: str | None
    display_name: str | None
    is_active: bool
    is_superuser: bool
    is_verified: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UpdateUserStatusRequest(BaseModel):
    """Admin request to change user status or grant superuser permissions."""

    is_active: bool | None = None
    is_superuser: bool | None = None


# --- AI Ledger Schemas ---

class AILedgerEntryResponse(BaseModel):
    """Single invocation record in the append-only ledger."""

    id: int
    owner_id: int
    user_id: int
    content_job_id: int | None
    provider: str
    model: str
    tokens_prompt: int
    tokens_completion: int
    cost_cents: int
    operation_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AILedgerSummaryResponse(BaseModel):
    """Aggregate statistics and item listing of AI ledger records."""

    total_invocations: int
    total_tokens_prompt: int
    total_tokens_completion: int
    total_cost_cents: int
    items: list[AILedgerEntryResponse]
